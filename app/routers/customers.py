from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, defer, selectinload
from sqlalchemy import or_

from app.auth import get_current_user
from app.database import get_db
from app.models import Bill, Customer, Payment, User
from app.opening_balance import apply_opening_balance
from app.schemas import (
    BulkOpeningBalanceIn,
    BulkOpeningBalanceOut,
    CustomerOut,
    CustomerWithBills,
    CustomerUpdate,
    OpeningBalanceIn,
    OpeningBalanceOut,
)

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


@router.put("/{customer_id}", response_model=CustomerOut)
def update_customer(
    customer_id: int,
    payload: CustomerUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Set or clear how much this customer may owe at once.

    A limit is a shop's own policy rather than an accounting fact, so nothing
    here touches the running totals — it only records the ceiling the billing
    screen warns against.
    """
    customer = (
        _owned(db, current_user).filter(Customer.id == customer_id).first()
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    if payload.clear_credit_limit:
        # Explicitly removing the limit, which a null value alone could not
        # tell apart from "not supplied".
        customer.credit_limit = None
    elif payload.credit_limit is not None:
        customer.credit_limit = payload.credit_limit

    db.commit()
    db.refresh(customer)
    return customer


def _opening_balance_out(customer: Customer) -> OpeningBalanceOut:
    return OpeningBalanceOut(
        customer_id=customer.id,
        amount=(
            float(customer.opening_balance)
            if customer.opening_balance is not None
            else None
        ),
        balance_type=customer.opening_balance_type,
        as_of=customer.opening_balance_date,
        reference=customer.opening_balance_ref,
        note=customer.opening_balance_note,
        total_unpaid=float(customer.total_unpaid or 0),
        advance_balance=float(customer.advance_balance or 0),
    )


@router.put("/{customer_id}/opening-balance", response_model=OpeningBalanceOut)
def set_opening_balance(
    customer_id: int,
    payload: OpeningBalanceIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    What this customer already owed when the shop left its paper book.

    Deliberately editable after the fact, and not locked once the customer
    starts trading. A one-person shop has nobody to ring when the figure it
    typed on migration day was wrong, and a balance it cannot correct is
    worse than one it can — the change is applied as a difference, so the
    running totals stay right either way.
    """
    customer = (
        db.query(Customer)
        .filter(Customer.id == customer_id, Customer.user_id == user.id)
        .first()
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    apply_opening_balance(
        customer,
        amount=payload.amount,
        balance_type=payload.balance_type,
        as_of=payload.as_of,
        reference=payload.reference,
        note=payload.note,
    )
    db.commit()
    db.refresh(customer)
    return _opening_balance_out(customer)


@router.post("/opening-balances", response_model=BulkOpeningBalanceOut)
def set_opening_balances(
    payload: BulkOpeningBalanceIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    A whole khata at once.

    The reason this exists rather than letting the app send one request per
    customer: a shop moving two hundred customers over a patchy mobile
    connection would otherwise be halfway migrated when the signal drops,
    with no way to tell which half. This commits once, so the migration
    either happened or it did not.
    """
    ids = [entry.customer_id for entry in payload.entries]
    owned = {
        customer.id: customer
        for customer in db.query(Customer).filter(
            Customer.id.in_(ids), Customer.user_id == user.id
        )
    }

    missing = [customer_id for customer_id in ids if customer_id not in owned]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Not your customers: {missing[:5]}",
        )

    results = []
    for entry in payload.entries:
        customer = owned[entry.customer_id]
        apply_opening_balance(
            customer,
            amount=entry.amount,
            balance_type=entry.balance_type,
            as_of=entry.as_of,
            reference=entry.reference,
            note=entry.note,
        )
        results.append(customer)

    db.commit()
    for customer in results:
        db.refresh(customer)

    return BulkOpeningBalanceOut(
        applied=len(results),
        results=[_opening_balance_out(customer) for customer in results],
    )
