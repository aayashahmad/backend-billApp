"""
Sales reporting.

Deliberately reports *sales*, not profit. Profit needs a cost price, and the
catalogue only records what a thing is sold for — bill lines are free text
that frequently never touch the catalogue at all. Calling revenue "profit"
would put a wrong number in front of someone making decisions with it.

Buckets are computed in the shop's local time, not UTC. Timestamps are stored
in UTC, and a sale rung up at 5am in India lands on the previous UTC day —
which would quietly move takings into yesterday's total.
"""
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Bill, Customer, Payment, User
from app.schemas import ReportBucket, ReportSummary, ReportTotals

router = APIRouter(prefix="/api/reports", tags=["reports"])

# How many buckets each period shows. Chosen to fit a phone screen: a bar
# chart with more than a couple of dozen columns is unreadable on 5 inches.
PERIOD_BUCKETS = {"daily": 14, "weekly": 12, "monthly": 12, "yearly": 5}


def _month_start(moment: datetime) -> datetime:
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _add_months(moment: datetime, count: int) -> datetime:
    month_index = moment.month - 1 + count
    year = moment.year + month_index // 12
    month = month_index % 12 + 1
    return moment.replace(year=year, month=month, day=1)


def _bucket_starts(period: str, now_local: datetime) -> List[datetime]:
    """The local start of each bucket, oldest first, ending with the current one."""
    count = PERIOD_BUCKETS[period]

    if period == "daily":
        today = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        return [today - timedelta(days=offset) for offset in range(count - 1, -1, -1)]

    if period == "weekly":
        # Weeks run Monday to Sunday, matching how shops talk about a week.
        monday = now_local.replace(
            hour=0, minute=0, second=0, microsecond=0
        ) - timedelta(days=now_local.weekday())
        return [monday - timedelta(weeks=offset) for offset in range(count - 1, -1, -1)]

    if period == "monthly":
        first = _month_start(now_local)
        return [_add_months(first, -offset) for offset in range(count - 1, -1, -1)]

    january = now_local.replace(
        month=1, day=1, hour=0, minute=0, second=0, microsecond=0
    )
    return [
        january.replace(year=january.year - offset)
        for offset in range(count - 1, -1, -1)
    ]


def _label(period: str, start: datetime) -> str:
    if period == "daily":
        return start.strftime("%d %b")
    if period == "weekly":
        return start.strftime("%d %b")
    if period == "monthly":
        return start.strftime("%b %Y")
    return start.strftime("%Y")


def _bucket_index(period: str, starts: List[datetime], moment: datetime) -> int:
    """Which bucket a local timestamp falls into, or -1 if it predates them all."""
    for index in range(len(starts) - 1, -1, -1):
        if moment >= starts[index]:
            return index
    return -1


@router.get("/summary", response_model=ReportSummary)
def sales_summary(
    period: str = Query("daily", pattern="^(daily|weekly|monthly|yearly)$"),
    # Minutes east of UTC — 330 for India. Sent by the client rather than
    # assumed, so a shop keeps its own day boundaries wherever it is.
    tz_offset: int = Query(0, ge=-840, le=840),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Sales, collections and dues bucketed over the requested period."""
    if period not in PERIOD_BUCKETS:
        raise HTTPException(status_code=422, detail="Unknown period")

    offset = timedelta(minutes=tz_offset)
    now_local = datetime.utcnow() + offset
    starts = _bucket_starts(period, now_local)
    window_start_utc = starts[0] - offset

    buckets: "OrderedDict[int, Dict[str, float]]" = OrderedDict(
        (index, {"bills": 0, "billed": 0.0, "collected": 0.0})
        for index in range(len(starts))
    )

    # Bills give what was sold and what was paid at the counter.
    bill_rows = (
        db.query(Bill.created_at, Bill.bill_total, Bill.amount_paid)
        .join(Customer, Customer.id == Bill.customer_id)
        .filter(
            Customer.user_id == current_user.id,
            Bill.created_at >= window_start_utc,
        )
        .all()
    )

    for created_at, bill_total, amount_paid in bill_rows:
        if created_at is None:
            continue
        index = _bucket_index(period, starts, created_at + offset)
        if index < 0:
            continue
        bucket = buckets[index]
        bucket["bills"] += 1
        bucket["billed"] += float(bill_total or 0)
        bucket["collected"] += float(amount_paid or 0)

    # Payments are money that arrived later against old dues. They are takings
    # for the day they land on, but they are not new sales — counting them as
    # billed would inflate turnover with the same rupees twice.
    payment_rows = (
        db.query(Payment.created_at, Payment.amount)
        .join(Customer, Customer.id == Payment.customer_id)
        .filter(
            Customer.user_id == current_user.id,
            Payment.created_at >= window_start_utc,
        )
        .all()
    )

    for created_at, amount in payment_rows:
        if created_at is None:
            continue
        index = _bucket_index(period, starts, created_at + offset)
        if index < 0:
            continue
        buckets[index]["collected"] += float(amount or 0)

    out_buckets = [
        ReportBucket(
            label=_label(period, starts[index]),
            starts_at=starts[index],
            bills=int(values["bills"]),
            billed=round(values["billed"], 2),
            collected=round(values["collected"], 2),
            # What this period added to the book, floored at zero: a period
            # where old dues were settled shows 0 rather than a negative.
            outstanding=round(max(values["billed"] - values["collected"], 0.0), 2),
        )
        for index, values in buckets.items()
    ]

    return ReportSummary(
        period=period,
        buckets=out_buckets,
        totals=ReportTotals(
            bills=sum(b.bills for b in out_buckets),
            billed=round(sum(b.billed for b in out_buckets), 2),
            collected=round(sum(b.collected for b in out_buckets), 2),
            outstanding=round(
                max(
                    sum(b.billed for b in out_buckets)
                    - sum(b.collected for b in out_buckets),
                    0.0,
                ),
                2,
            ),
        ),
    )
