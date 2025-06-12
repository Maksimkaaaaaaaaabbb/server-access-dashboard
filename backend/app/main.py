import os
import time
import threading
from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    Query,
    BackgroundTasks,
    Security,
    status,
)
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func, asc, desc
from typing import List, Optional
from contextlib import asynccontextmanager
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler


from . import models, log_collector
from .models import LogEntryRead, PaginatedLogResponse
from .database import init_db, get_db

logger = log_collector.logger
scheduler = BackgroundScheduler(daemon=True)

# --- API Key Setup ---
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
EXPECTED_API_KEY = os.getenv("API_KEY", "default_insecure_key_CHANGE_ME")

if EXPECTED_API_KEY == "default_insecure_key_CHANGE_ME":
    logger.warning(
        "!!! SECURITY WARNING: API_KEY environment variable not set or using default value. Please define a secure key! !!!"
    )


async def verify_api_key(api_key_header: Optional[str] = Security(API_KEY_HEADER)):
    if not api_key_header:
        logger.warning("API key missing in header.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key missing in X-API-Key header",
        )
    if api_key_header != EXPECTED_API_KEY:
        logger.warning("Invalid API key received.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key"
        )
    return api_key_header


# --- Status management for log collection ---
log_collection_status = "idle"
status_lock = threading.Lock()


def set_collection_status(new_status: str):
    global log_collection_status
    with status_lock:
        log_collection_status = new_status
    logger.info(f"Log collection status changed to: {new_status}")


def get_collection_status() -> str:
    with status_lock:
        return log_collection_status


def run_log_collection_with_status():
    if get_collection_status() == "running":
        logger.warning("Log collection is already running. Skipping new trigger.")
        return
    logger.info("Starting background log collection...")
    # Set status to running before starting the collection
    set_collection_status("running")
    try:
        log_collector.process_log_files()
        set_collection_status("finished")
    except Exception as e:
        logger.error(f"Error during background log collection: {e}", exc_info=True)
        set_collection_status("error")


# --- End of status management ---


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting FastAPI application...")
    logger.info("Initializing database...")
    init_db()  # Ensure that check_same_thread=False is set in the engine!
    logger.info("Database initialized.")
    set_collection_status("idle")  # Initialize status at startup
    try:
        interval_minutes = int(os.getenv("LOG_COLLECTION_INTERVAL_MINUTES", "10"))
        logger.info(f"Scheduling log collection every {interval_minutes} minutes.")
        scheduler.add_job(
            run_log_collection_with_status,
            "interval",
            minutes=interval_minutes,
            id="log_collection_job",
            replace_existing=True,
        )
        scheduler.start()
        logger.info("Scheduler started.")
    except Exception as e:
        logger.error(f"Error starting scheduler: {e}", exc_info=True)
    yield
    logger.info("Shutting down FastAPI application...")
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler shut down.")
    log_collector.close_geoip_reader()
    logger.info("Application stopped.")


app = FastAPI(
    title="Server Access Dashboard API",
    description="API for retrieving and managing server access logs",
    version="1.0.0",
    lifespan=lifespan,
)

# --- CORS Middleware ---
origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "X-API-Key"],
)

# --- API Routes ---


# Manual Trigger Endpoint
@app.post(
    "/collect-logs/",
    status_code=202,
    summary="Manually trigger log collection",
    dependencies=[Depends(verify_api_key)],
)
async def trigger_log_collection(background_tasks: BackgroundTasks):
    current_status = get_collection_status()
    if current_status == "running":
        logger.warning("Manual trigger ignored: Log collection is already running.")
        # Return a specific status so the frontend knows it's already running
        # Or raise a 409 Conflict error
        # return {"message": "Log collection is already running."}
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Log collection is already running.",
        )
    logger.info("Manual trigger for log collection received (authenticated).")
    background_tasks.add_task(run_log_collection_with_status)
    return {"message": "Log collection process started in the background."}


# Status Endpoint
class CollectionStatusResponse(BaseModel):
    status: str


@app.get(
    "/collect-logs/status",
    response_model=CollectionStatusResponse,
    summary="Retrieve current status of log collection",
    dependencies=[Depends(verify_api_key)],
)
async def get_log_collection_status():
    current_status = get_collection_status()
    # Status is only reset if it is finished/error,
    # so that polling does not immediately see "idle" again.
    if current_status in ["finished", "error"]:
        set_collection_status("idle")
    return CollectionStatusResponse(status=current_status)


# PaginatedLogResponse Modell
class PaginatedLogResponse(BaseModel):
    logs: List[LogEntryRead]
    total_count: int


# GET /logs/ Endpoint
@app.get(
    "/logs/",
    response_model=PaginatedLogResponse,
    summary="Retrieve processed log entries",
    dependencies=[Depends(verify_api_key)],
)
async def read_logs(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    ip_address: Optional[str] = Query(None),
    country: Optional[str] = Query(None),
    domain: Optional[str] = Query(None),
    status_code: Optional[int] = Query(None),
    sort_by: Optional[str] = Query("timestamp"),
    sort_dir: Optional[str] = Query("desc"),
    db: Session = Depends(get_db),
):
    logger.debug("GET /logs/ called...")
    try:
        base_query = db.query(models.LogEntry)
        if ip_address:
            base_query = base_query.filter(
                models.LogEntry.ip_address.ilike(f"%{ip_address}%")
            )
        if country:
            base_query = base_query.filter(
                models.LogEntry.country.ilike(f"%{country}%")
            )
        if domain:
            base_query = base_query.filter(models.LogEntry.domain.ilike(f"%{domain}%"))
        if status_code is not None:
            base_query = base_query.filter(models.LogEntry.status_code == status_code)
        total_count = base_query.count()
        sort_column = getattr(models.LogEntry, sort_by, models.LogEntry.timestamp)
        ordered_query = base_query.order_by(
            asc(sort_column) if sort_dir == "asc" else desc(sort_column)
        )
        logs = ordered_query.offset(skip).limit(limit).all()
        return PaginatedLogResponse(logs=logs, total_count=total_count)
    except Exception as e:
        logger.error(f"Error retrieving logs: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Internal server error while retrieving logs."
        )


# Country Summary Endpunkt
class CountrySummary(BaseModel):
    country: Optional[str] = "Unknown"
    count: int


@app.get(
    "/logs/summary/by-country/",
    response_model=List[CountrySummary],
    summary="Summarize accesses per country",
    dependencies=[Depends(verify_api_key)],
)
async def get_summary_by_country(db: Session = Depends(get_db)):
    logger.debug("GET /logs/summary/by-country/ called")
    try:
        summary_query = (
            db.query(
                models.LogEntry.country, func.count(models.LogEntry.id).label("count")
            )
            .group_by(models.LogEntry.country)
            .order_by(func.count(models.LogEntry.id).desc())
        )
        summary_results_raw = summary_query.all()
        summary_results = [
            CountrySummary(
                country=country if country is not None else "Unknown", count=count
            )
            for country, count in summary_results_raw
        ]
        return summary_results
    except Exception as e:
        logger.error(f"Error retrieving country summary: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving country summary.",
        )


# Start the app (for local development)
if __name__ == "__main__":
    import uvicorn

    try:
        from dotenv import load_dotenv

        dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
        if os.path.exists(dotenv_path):
            load_dotenv(dotenv_path=dotenv_path)
            print(f"Loaded local .env file from: {dotenv_path}")
        else:
            print("No local .env file found.")
        EXPECTED_API_KEY = os.getenv("API_KEY", "default_insecure_key_CHANGE_ME")
        if EXPECTED_API_KEY == "default_insecure_key_CHANGE_ME":
            logger.warning(
                "!!! API_KEY not found in .env or set to default. Using insecure default! !!!"
            )
    except ImportError:
        print("python-dotenv not installed, .env file will not be loaded.")
    print("Starting Uvicorn development server at http://0.0.0.0:8000")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
