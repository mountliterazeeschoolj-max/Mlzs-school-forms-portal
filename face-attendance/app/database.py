"""SQLAlchemy engine/session for the Supabase Postgres database (tables are created by the Supabase migration)."""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL


class Base(DeclarativeBase):
    pass


if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set (use your Supabase Postgres connection string).")
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=5)
SessionLocal = sessionmaker(bind=engine, autoflush=False)


def get_db():
    """FastAPI dependency: one session per request."""
    with SessionLocal() as db:
        yield db


def check_db():
    """Fail fast at startup if the database or the face tables are missing."""
    with engine.connect() as c:
        c.execute(text("select 1 from public.face_cameras limit 1"))
