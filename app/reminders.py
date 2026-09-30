"""
Who needs chasing, and how they get told.

The rules live here rather than in the router so the daily job and the app's
own "who is overdue" list cannot drift apart — one of them deciding somebody
is overdue while the other disagrees is how a shopkeeper stops trusting the
screen.

Nothing in here sends anything unless the shop has turned reminders on.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.mailer import is_configured, send_balance_reminder
from app.models import Bill, Customer, Payment, User

logger = logging.getLogger(__name__)

# Never chase the same person twice inside this window, whatever the rules
# say. A reminder every night is harassment, not bookkeeping.
MIN_DAYS_BETWEEN_REMINDERS = 3


def _threshold(customer: Customer, owner: User) -> float:
    """The customer's own floor, falling back to the shop's."""
    if customer.reminder_min_amount is not None:
        return float(customer.reminder_min_amount)
    return float(owner.reminder_min_amount or 0)


def _age_days(customer: Customer, owner: User) -> int:
    if customer.reminder_after_days is not None:
        return int(customer.reminder_after_days)
    return int(owner.reminder_after_days or 0)


def oldest_unsettled_at(db: Session, customer: Customer):
    """
    When this debt started.

    Taken from the oldest bill that left something owing, because that is
    what "42 days overdue" has to mean to a shopkeeper — not the date of the
    most recent sale, which would reset the clock every time the customer
    bought a packet of tea.

    A balance carried over from the shop's paper book counts too, and has
    to: a migrated customer has no bills at all, so without this the oldest
    debts in the shop — the ones that drove it to buy an app — would be the
    only ones it never chased.
    """
    oldest_bill = (
        db.query(func.min(Bill.created_at))
        .filter(Bill.customer_id == customer.id, Bill.unbalance > 0)
        .scalar()
    )

    carried_over = (
        customer.opening_balance_date
        if customer.opening_balance
        and customer.opening_balance_type == "due"
        else None
    )

    candidates = [when for when in (oldest_bill, carried_over) if when]
    return min(candidates) if candidates else None


def due_customers(db: Session, owner: User, now: datetime = None):
    """
    Everyone of this shop's customers who has crossed their reminder rules.

    Returns (customer, outstanding, days_overdue) newest debt last, so the
    longest-standing debt is at the top where it belongs.
    """
    now = now or datetime.utcnow()

    candidates = (
        db.query(Customer)
        .filter(
            Customer.user_id == owner.id,
            Customer.reminder_enabled.is_(True),
            Customer.total_unpaid > 0,
        )
        .all()
    )

    due = []
    for customer in candidates:
        outstanding = float(customer.total_unpaid or 0)
        if outstanding < _threshold(customer, owner):
            continue

        since = oldest_unsettled_at(db, customer)
        if since is None:
            continue

        days = (now - since).days
        if days < _age_days(customer, owner):
            continue

        due.append((customer, outstanding, days))

    due.sort(key=lambda row: row[2], reverse=True)
    return due


def _may_send_again(customer: Customer, now: datetime) -> bool:
    if customer.last_reminded_at is None:
        return True
    return (now - customer.last_reminded_at).days >= MIN_DAYS_BETWEEN_REMINDERS


def send_due_reminders(db: Session, owner: User, now: datetime = None):
    """
    Emails the customers who are due one and have an address on file.

    Returns (emailed, skipped) so the caller can tell the owner how many
    still need a message sent by hand. A customer with no email is not a
    failure — it is the ordinary case, and they stay on the owner's list.
    """
    now = now or datetime.utcnow()
    emailed = []
    skipped = []

    for customer, outstanding, days in due_customers(db, owner, now):
        if not customer.email or not is_configured():
            skipped.append(customer)
            continue

        if not _may_send_again(customer, now):
            continue

        try:
            send_balance_reminder(
                to=customer.email,
                customer_name=customer.name,
                shop_name=owner.business_name or owner.username,
                outstanding=outstanding,
                days_overdue=days,
                upi_id=owner.upi_id,
                whatsapp=owner.whatsapp_number or owner.business_phone,
            )
        except Exception:  # noqa: BLE001 — providers raise many types
            # One bad address must not stop the rest of the run.
            logger.exception("Reminder email failed for customer %s", customer.id)
            skipped.append(customer)
            continue

        customer.last_reminded_at = now
        emailed.append(customer)

    db.commit()
    return emailed, skipped
