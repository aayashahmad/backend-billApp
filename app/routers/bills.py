import os
import time
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Customer, Bill, User
from app.schemas import BillCreateResponse

router = APIRouter(prefix="/api/bills", tags=["bills"])

UPLOADS_DIR = os.getenv("UPLOADS_DIR", "uploads")


# Registered on both "" and "/" so a multipart POST is never answered with a
# 307 redirect — some HTTP clients drop the body when replaying a redirect.
@router.post("", response_model=BillCreateResponse, status_code=201)
@router.post("/", response_model=BillCreateResponse, status_code=201,
             include_in_schema=False)
async def create_bill(
    phone: str = Form(...),
    customer_name: str = Form(""),
    item_name: str = Form(...),
    qty: int = Form(...),
    rate: float = Form(...),
    payment_type: str = Form(...),
    amount_paid: Optional[float] = Form(None),
    transaction_number: Optional[str] = Form(None),
    transaction_screenshot: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a bill — finds or creates the customer, updates running totals."""
    bill_total = round(qty * rate, 2)

    # Find or create the customer *within this owner's* book. Scoping the
    # lookup by user_id is what keeps two shops that bill the same phone
    # number from sharing a customer record and its running totals.
    customer = (
        db.query(Customer)
        .filter(Customer.user_id == current_user.id, Customer.phone == phone)
        .first()
    )
    if not customer:
        customer = Customer(
            user_id=current_user.id, name=customer_name, phone=phone
        )
        db.add(customer)
        db.flush()  # get customer.id before creating bill
    elif customer_name and customer.name != customer_name:
        customer.name = customer_name

    # Handle screenshot upload for online payments
    screenshot_url = None
    if transaction_screenshot and transaction_screenshot.filename:
        os.makedirs(UPLOADS_DIR, exist_ok=True)
        ext = os.path.splitext(transaction_screenshot.filename)[1]
        filename = f"txn_{int(time.time() * 1_000_000)}{ext}"
        screenshot_url = f"{UPLOADS_DIR}/{filename}"
        contents = await transaction_screenshot.read()
        with open(screenshot_url, "wb") as f:
            f.write(contents)

    # Calculate unbalance based on payment type
    unbalance = 0.0
    if payment_type == "cash":
        paid = amount_paid or 0.0
        unbalance = round(bill_total - paid, 2)
    elif payment_type == "online":
        amount_paid = bill_total
        unbalance = 0.0

    bill = Bill(
        customer_id=customer.id,
        item_name=item_name,
        qty=qty,
        rate=rate,
        bill_total=bill_total,
        payment_type=payment_type,
        amount_paid=amount_paid,
        unbalance=unbalance,
        transaction_number=transaction_number,
        transaction_screenshot_url=screenshot_url,
    )
    db.add(bill)

    # Update customer running totals
    customer.total_amount = float(customer.total_amount or 0) + bill_total
    customer.total_unpaid = float(customer.total_unpaid or 0) + unbalance

    db.commit()
    db.refresh(bill)
    db.refresh(customer)

    return BillCreateResponse(bill=bill, customer=customer)
