"""
Payment reminders: settings, the overdue list, and the daily run.

The daily run is a plain HTTP endpoint rather than a scheduler inside the
app, because the free hosting tier sleeps an idle service and has no cron.
An external pinger calls it once a day with a shared secret; that call also
wakes the service, which is why it is the first thing the shop's morning
depends on.
"""

import logging
import os
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Customer, User
from app.push import send_push
from app.reminders import due_customers, send_due_reminders
from app.schemas import (
    DueCustomerOut,
    PushTokenIn,
    ReminderRunOut,
    ReminderSettingsIn,
    ReminderSettingsOut,
    ShopReminderSettingsIn,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reminders", tags=["reminders"])


def _row(customer: Customer, outstanding: float, days: int) -> DueCustomerOut:
    return DueCustomerOut(
        id=customer.id,
        name=customer.name,
        phone=customer.phone,
        email=customer.email,
        outstanding=round(outstanding, 2),
        days_overdue=days,
        last_reminded_at=customer.last_reminded_at,
    )


@router.get("/due", response_model=list[DueCustomerOut])
def list_due(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Who needs chasing right now.

    Answers regardless of the master switch: the owner asking who is overdue
    is a different question from whether the shop sends anything on its own,
    and the list is useful even to a shop that only ever messages by hand.
    """
    return [_row(c, amount, days) for c, amount, days in due_customers(db, user)]


@router.get("/settings", response_model=ReminderSettingsOut)
def read_settings(user: User = Depends(get_current_user)):
    return ReminderSettingsOut(
        reminders_enabled=user.reminders_enabled,
        reminder_min_amount=float(user.reminder_min_amount or 0),
        reminder_after_days=int(user.reminder_after_days or 0),
        push_registered=bool(user.expo_push_token),
    )


@router.put("/settings", response_model=ReminderSettingsOut)
def update_settings(
    payload: ShopReminderSettingsIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if payload.reminders_enabled is not None:
        user.reminders_enabled = payload.reminders_enabled
    if payload.reminder_min_amount is not None:
        user.reminder_min_amount = payload.reminder_min_amount
    if payload.reminder_after_days is not None:
        user.reminder_after_days = payload.reminder_after_days

    db.commit()
    db.refresh(user)
    return read_settings(user)


@router.put("/push-token")
def register_push_token(
    payload: PushTokenIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The device this shop wants its daily summary on."""
    user.expo_push_token = payload.token or None
    db.commit()
    return {"registered": bool(user.expo_push_token)}


@router.put("/customers/{customer_id}", response_model=DueCustomerOut)
def update_customer_reminder(
    customer_id: int,
    payload: ReminderSettingsIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Per-customer overrides, including switching them off entirely."""
    customer = (
        db.query(Customer)
        .filter(Customer.id == customer_id, Customer.user_id == user.id)
        .first()
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    fields = payload.model_dump(exclude_unset=True)
    if "email" in fields:
        customer.email = (fields["email"] or "").strip().lower() or None
    if "reminder_enabled" in fields:
        customer.reminder_enabled = bool(fields["reminder_enabled"])
    # None is a real value for these two: it means "fall back to the shop's
    # setting", which is why exclude_unset matters above — an absent field
    # must leave the override alone rather than clearing it.
    if "reminder_min_amount" in fields:
        customer.reminder_min_amount = fields["reminder_min_amount"]
    if "reminder_after_days" in fields:
        customer.reminder_after_days = fields["reminder_after_days"]

    db.commit()
    db.refresh(customer)
    return _row(customer, float(customer.total_unpaid or 0), 0)


@router.post("/run", response_model=ReminderRunOut)
def run_daily(
    x_reminder_secret: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """
    The daily pass over every shop. Called by an external scheduler.

    Guarded by a shared secret rather than a user token: nobody is signed in
    at 6am, and an unguarded endpoint that emails customers is an open relay
    pointed at a shop's own customer list.
    """
    expected = os.getenv("REMINDER_SECRET") or ""
    if not expected:
        raise HTTPException(
            status_code=503, detail="Reminder runs are not configured on this server."
        )
    if x_reminder_secret != expected:
        raise HTTPException(status_code=403, detail="Not allowed.")

    now = datetime.utcnow()
    shops_processed = 0
    emails_sent = 0
    owners_notified = 0

    # Only shops that asked for this. A shop with the switch off still gets
    # nothing, however overdue its customers are.
    owners = db.query(User).filter(User.reminders_enabled.is_(True)).all()

    for owner in owners:
        shops_processed += 1
        try:
            emailed, skipped = send_due_reminders(db, owner, now)
        except Exception:  # noqa: BLE001 — one shop must not stop the rest
            logger.exception("Reminder run failed for shop %s", owner.id)
            continue

        emails_sent += len(emailed)

        outstanding_count = len(emailed) + len(skipped)
        if outstanding_count and owner.expo_push_token:
            body = f"{outstanding_count} customer"
            body += "s need chasing" if outstanding_count != 1 else " needs chasing"
            if emailed:
                body += f" · {len(emailed)} emailed"
            if send_push(
                owner.expo_push_token,
                "Payment reminders",
                body,
                {"screen": "Reminders"},
            ):
                owners_notified += 1

    return ReminderRunOut(
        shops_processed=shops_processed,
        emails_sent=emails_sent,
        owners_notified=owners_notified,
    )
