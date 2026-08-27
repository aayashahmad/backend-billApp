import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

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
