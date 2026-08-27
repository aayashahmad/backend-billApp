import os
import time
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Item
from app.schemas import ItemOut

router = APIRouter(prefix="/api/items", tags=["items"])

UPLOADS_DIR = "uploads"


@router.get("/", response_model=list[ItemOut])
def list_items(db: Session = Depends(get_db)):
    """Return all items, newest first."""
    return db.query(Item).order_by(Item.id.desc()).all()


@router.post("/", response_model=ItemOut, status_code=201)
async def create_item(
    name: str = Form(...),
    description: str = Form(""),
    image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    """Create a new item with optional image upload."""
    image_path = ""

    if image and image.filename:
        os.makedirs(UPLOADS_DIR, exist_ok=True)
        ext = os.path.splitext(image.filename)[1]
        filename = f"{int(time.time() * 1_000_000)}{ext}"
        image_path = f"{UPLOADS_DIR}/{filename}"

        contents = await image.read()
        with open(image_path, "wb") as f:
            f.write(contents)

    db_item = Item(name=name, description=description, image=image_path)
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return db_item
