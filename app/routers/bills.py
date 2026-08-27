import mimetypes
import os
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
)
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Customer, Bill, User
from app.schemas import BillCreateResponse

router = APIRouter(prefix="/api/bills", tags=["bills"])

UPLOADS_DIR = os.getenv("UPLOADS_DIR", "uploads")

# Screenshots live in Postgres, so an unbounded upload would bloat the row
# and the backups along with it.
MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024


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

    # Screenshots are stored in the database, not on disk — hosted
    # filesystems are ephemeral and would lose them on every deploy.
    screenshot_data = None
    screenshot_mime = None
    if transaction_screenshot and transaction_screenshot.filename:
        screenshot_data = await transaction_screenshot.read()

        if len(screenshot_data) > MAX_SCREENSHOT_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    "Screenshot is too large "
                    f"({len(screenshot_data) // 1024} KB). "
                    f"Maximum is {MAX_SCREENSHOT_BYTES // 1024} KB."
                ),
            )

        screenshot_mime = (
            transaction_screenshot.content_type
            or mimetypes.guess_type(transaction_screenshot.filename)[0]
            or "application/octet-stream"
        )

        if not screenshot_mime.startswith("image/"):
            raise HTTPException(
                status_code=415,
                detail=f"Expected an image, received {screenshot_mime}.",
            )

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
        screenshot_data=screenshot_data,
        screenshot_mime=screenshot_mime,
    )
    db.add(bill)

    # Update customer running totals
    customer.total_amount = float(customer.total_amount or 0) + bill_total
    customer.total_unpaid = float(customer.total_unpaid or 0) + unbalance

    db.commit()
    db.refresh(bill)
    db.refresh(customer)

    return BillCreateResponse(bill=bill, customer=customer)


@router.get("/{bill_id}/screenshot")
def get_bill_screenshot(
    bill_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Payment screenshot for one bill.

    Joined through Customer so an owner can only fetch screenshots attached to
    their own bills — these are payment records, and the previous static mount
    served them to anyone holding the URL.
    """
    bill = (
        db.query(Bill)
        .join(Customer, Bill.customer_id == Customer.id)
        .filter(Bill.id == bill_id, Customer.user_id == current_user.id)
        .first()
    )
    if not bill or bill.screenshot_data is None:
        raise HTTPException(status_code=404, detail="Screenshot not found")

    return Response(
        content=bill.screenshot_data,
        media_type=bill.screenshot_mime or "application/octet-stream",
        # Private: the image is per-owner, so shared caches must not keep it.
        headers={"Cache-Control": "private, max-age=3600"},
    )
