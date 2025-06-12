from sqlalchemy.orm import Session
from .models import Base, engine, SessionLocal


def init_db():
    """
    Initialize database by creating all tables inherited from Base.
    This function should be called once during application startup.
    """
    try:
        Base.metadata.create_all(bind=engine)
        print("Database tables successfully checked/created.")
    except Exception as e:
        print(f"Error creating database tables: {e}")
        raise


def get_db():
    """
    FastAPI dependency that provides a database session for each request.
    Yields:
        Session: Database session that is automatically closed after request completion.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
