"""
Database session and engine management for Supabase PostgreSQL.
Uses SQLAlchemy 2.0 and psycopg v3.
"""

from typing import Generator
from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from backend.core.config import settings

# Explicit PostgreSQL naming convention for deterministic constraint/index naming in Alembic
POSTGRES_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=POSTGRES_NAMING_CONVENTION)

# Construct connection arguments tailored for Supabase
connect_args = {}

# Ensure SSL requirement is passed to psycopg if not already present in the query string
if "sslmode=" not in settings.DATABASE_URL:
    connect_args["sslmode"] = "require"

# Supabase Transaction Pooler (port 6543) does not support prepared statements
if settings.USE_TRANSACTION_POOLER:
    connect_args["prepare_threshold"] = None

# Create SQLAlchemy 2.0 engine with connection pooling optimized for Supabase free-tier limits
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_recycle=settings.DB_POOL_RECYCLE,
    connect_args=connect_args,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

Base = declarative_base(metadata=metadata)


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency that yields a SQLAlchemy database session per request
    and guarantees proper closure upon completion.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

