from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import User
from app.schemas import BusinessProfileOut, BusinessProfileUpdate

router = APIRouter(prefix="/api/business", tags=["business"])


@router.get("", response_model=BusinessProfileOut)
@router.get("/", response_model=BusinessProfileOut, include_in_schema=False)
def get_business_profile(current_user: User = Depends(get_current_user)):
    """Letterhead for the signed-in owner."""
    return current_user


@router.put("", response_model=BusinessProfileOut)
@router.put("/", response_model=BusinessProfileOut, include_in_schema=False)
def update_business_profile(
    payload: BusinessProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Replace the owner's letterhead.

    The screen submits every field, so a blank value is a deliberate clear —
    stored as NULL rather than an empty string so documents can fall back on
    the account details.
    """
    for field, value in payload.model_dump(exclude_unset=True).items():
        cleaned = value.strip() if isinstance(value, str) else value
        setattr(current_user, field, cleaned or None)

    # First successful save completes onboarding; later edits leave the
    # original timestamp alone.
    if current_user.onboarded_at is None:
        current_user.onboarded_at = datetime.utcnow()

    db.commit()
    db.refresh(current_user)
    return current_user
