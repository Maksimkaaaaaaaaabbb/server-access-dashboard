from sqlalchemy import create_engine, Column, Integer, String, DateTime

try:
    from sqlalchemy.orm import declarative_base

    Base = declarative_base()
except ImportError:
    from sqlalchemy.ext.declarative import declarative_base

    Base = declarative_base()

from sqlalchemy.orm import sessionmaker
from pydantic import BaseModel
from datetime import datetime
import os
from dotenv import load_dotenv
from typing import List

dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=dotenv_path)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./logs.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class LogEntry(Base):
    __tablename__ = "log_entries"
    id = Column(Integer, primary_key=True, index=True)
    ip_address = Column(String, index=True)
    timestamp = Column(DateTime, index=True)
    status_code = Column(Integer, index=True, nullable=True)
    country = Column(String, index=True, nullable=True)
    request_path = Column(String, nullable=True)
    domain = Column(String, nullable=True, index=True)
    raw_log = Column(String)


class LogEntryRead(BaseModel):
    id: int
    ip_address: str
    status_code: int | None
    timestamp: datetime
    country: str | None
    request_path: str | None
    domain: str | None
    raw_log: str

    class Config:
        from_attributes = True


class PaginatedLogResponse(BaseModel):
    logs: List[LogEntryRead]
    total_count: int
