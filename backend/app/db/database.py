from __future__ import annotations

import os
from collections.abc import Generator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# Hosted PostgreSQL providers commonly return a plain postgresql:// URI.
# Use the psycopg 3 SQLAlchemy dialect explicitly so the application does
# not depend on a psycopg2 installation.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgres://") :]
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgresql://") :]


class Base(DeclarativeBase):
    """Base class for all PostgreSQL-backed PIPSGOX models."""


if DATABASE_URL:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
else:
    engine = None
    SessionLocal = None


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency for a PostgreSQL session."""
    if SessionLocal is None:
        raise RuntimeError("PostgreSQL is not configured. Set DATABASE_URL first.")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
