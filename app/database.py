import os

from dotenv import load_dotenv
# pyrefly: ignore [missing-import]
from sqlalchemy import create_engine
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Copy .env.example to .env for local work; "
        "on Render it is injected from the database defined in render.yaml."
    )

# Some managed providers (Render, Heroku) still hand out the legacy
# "postgres://" scheme, which SQLAlchemy 2.x refuses to parse.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    # Managed Postgres drops idle connections; without this the first request
    # after a quiet period fails on a stale pooled connection.
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Yield a DB session and close it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
