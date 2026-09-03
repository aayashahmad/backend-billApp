from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, defer, selectinload
from sqlalchemy import or_

from app.auth import get_current_user
from app.database import get_db
from app.models import Bill, Customer, Payment, User
from app.schemas import CustomerOut, CustomerWithBills

router = APIRouter(prefix="/api/customers", tags=["customers"])


def _owned(db: Session, user: User):
    """Base query restricted to the signed-in owner's customers."""
    return db.query(Customer).filter(Customer.user_id == user.id)


@router.get("", response_model=List[CustomerOut])
@router.get("/", response_model=List[CustomerOut], include_in_schema=False)
def list_customers(
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Every customer belonging to the signed-in owner, alphabetically."""
    return (
        _owned(db, current_user)
        .order_by(Customer.name.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )


@router.get("/by-phone/{phone}", response_model=CustomerOut)
def get_customer_by_phone(
    phone: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lookup within the owner's own customers.

    A 404 here means "new to this shop" — another owner's customer with the
    same phone must not be revealed, so the scoped query handles both cases.
    """
    customer = _owned(db, current_user).filter(Customer.phone == phone).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.get("/search", response_model=List[CustomerOut])
def search_customers(
    q: str = Query(..., min_length=1),
    # The bill form shows these in a scrollable picker, where 20 is easy to
    # run past — a shop with a dozen Sharmas would never see the last one.
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Search the owner's customers by name or phone (partial match)."""
    # Escape the LIKE wildcards, or a customer typing "100%" matches everyone.
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    return (
        _owned(db, current_user)
        .filter(
            or_(
                Customer.name.ilike(pattern, escape="\\"),
                Customer.phone.ilike(pattern, escape="\\"),
            )
        )
        .order_by(Customer.name.asc())
        .limit(limit)
        .all()
    )


@router.get("/{customer_id}", response_model=CustomerWithBills)
def get_customer_detail(
    customer_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Full profile with bill history — 404 unless the caller owns it."""
    customer = (
        _owned(db, current_user)
        .options(
            # selectinload keeps this at three queries however many bills
            # there are; lazy loading ran one query per bill for its items.
            selectinload(Customer.bills)
            .options(defer(Bill.screenshot_data))
            .selectinload(Bill.items),
            # The response only describes payments — deferring the image
            # column stops every screenshot blob (up to 5MB each) from being
            # pulled into memory on every profile view.
            selectinload(Customer.payments).options(
                defer(Payment.screenshot_data)
            ),
        )
        .filter(Customer.id == customer_id)
        .first()
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer
