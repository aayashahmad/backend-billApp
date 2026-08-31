import mimetypes
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
from app.models import Customer, Payment, User
from app.schemas import PaymentOut

router = APIRouter(tags=["payments"])

# Same ceiling as a bill screenshot: these rows live in Postgres.
MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024

PAYMENT_CASH = "cash"
PAYMENT_ONLINE = "online"
PAYMENT_CHEQUE = "cheque"
PAYMENT_TYPES = (PAYMENT_CASH, PAYMENT_ONLINE, PAYMENT_CHEQUE)
REFERENCE_TYPES = (PAYMENT_ONLINE, PAYMENT_CHEQUE)


@router.post(
    "/api/customers/{customer_id}/payments",
    response_model=PaymentOut,
    status_code=201,
)
async def record_payment(
    customer_id: int,
    amount: float = Form(...),
    payment_type: str = Form(...),
    transaction_number: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
    transaction_screenshot: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Record money received against a customer's outstanding balance.

    This settles dues rather than recording a sale, so it moves
    `total_unpaid` only — `total_amount` is what the customer was billed and
    must not change when they pay.
    """
    customer = (
        db.query(Customer)
        .filter(Customer.id == customer_id, Customer.user_id == current_user.id)
        .first()
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    if payment_type not in PAYMENT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown payment type '{payment_type}'. "
                f"Expected one of: {', '.join(PAYMENT_TYPES)}."
            ),
        )

    if payment_type in REFERENCE_TYPES and not (transaction_number or "").strip():
        label = "Cheque number" if payment_type == PAYMENT_CHEQUE else "Transaction number"
        raise HTTPException(status_code=422, detail=f"{label} is required.")

    if amount <= 0:
        raise HTTPException(
            status_code=422, detail="Payment amount must be greater than zero."
        )

    outstanding = float(customer.total_unpaid or 0)
    if outstanding <= 0:
        raise HTTPException(
            status_code=422,
            detail=f"{customer.name} has nothing outstanding to pay.",
        )

    # Taking more than is owed would push the balance negative, which every
    # screen reading it would then render as nonsense.
    if round(amount, 2) > round(outstanding, 2):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Payment ({amount:.2f}) is more than the "
                f"{outstanding:.2f} outstanding."
            ),
        )

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

    payment = Payment(
        customer_id=customer.id,
        amount=round(amount, 2),
        payment_type=payment_type,
        transaction_number=(transaction_number or "").strip() or None,
        note=(note or "").strip() or None,
        screenshot_data=screenshot_data,
        screenshot_mime=screenshot_mime,
    )
    db.add(payment)

    customer.total_unpaid = max(round(outstanding - amount, 2), 0.0)

    db.commit()
    db.refresh(payment)
    return payment


@router.get("/api/payments/{payment_id}/screenshot")
def get_payment_screenshot(
    payment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Proof of payment, joined through Customer so only its owner can read it."""
    payment = (
        db.query(Payment)
        .join(Customer, Payment.customer_id == Customer.id)
        .filter(Payment.id == payment_id, Customer.user_id == current_user.id)
        .first()
    )
    if not payment or payment.screenshot_data is None:
        raise HTTPException(status_code=404, detail="Screenshot not found")

    return Response(
        content=payment.screenshot_data,
        media_type=payment.screenshot_mime or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=3600"},
    )
