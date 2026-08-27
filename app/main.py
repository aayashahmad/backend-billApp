import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.database import engine
from app.models import Base
from app.routers import auth, business, customers, bills

# Create all tables on startup.
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Billing API")

# CORS — allow frontend dev server on any port.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve uploaded images as static files. The directory is created first —
# StaticFiles raises on startup if it is missing, which is the normal state
# of a freshly deployed checkout.
UPLOADS_DIR = os.getenv("UPLOADS_DIR", "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")

# Include routers.
app.include_router(auth.router)
app.include_router(business.router)
app.include_router(customers.router)
app.include_router(bills.router)


@app.get("/", tags=["health"])
def root():
    """
    Signpost for anyone opening the server root in a browser.

    Every real endpoint lives under /api, so without this the root returns a
    bare 404 that reads like the server is broken when it is fine.
    """
    return {
        "service": "Billing API",
        "status": "ok",
        "docs": "/docs",
        "health": "/health",
        "api_root": "/api",
    }


@app.get("/health", tags=["health"])
def health():
    """
    Liveness + database check.

    Returns 503 rather than 200 when the database is unreachable, so this is
    safe to point a container healthcheck or uptime monitor at.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        database = "ok"
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "database": f"unreachable: {exc}"},
        )

    return {"status": "ok", "database": database}
