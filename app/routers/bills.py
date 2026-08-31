import json
import mimetypes
import os
from typing import Any, List, Optional

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
from app.models import Customer, Bill, BillItem, User
from app.schemas import BillCreateResponse

router = APIRouter(prefix="/api/bills", tags=["bills"])

UPLOADS_DIR = os.getenv("UPLOADS_DIR", "uploads")

# Screenshots live in Postgres, so an unbounded upload would bloat the row
# and the backups along with it.
MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024

# Cash and cheque record an amount the user enters; an online transfer settles
# the bill in full. Cheque is treated like cash on purpose — it is often
# written for part of the balance and it can bounce, so recording the figure
# is more truthful than assuming settlement.
PAYMENT_CASH = "cash"
PAYMENT_ONLINE = "online"
PAYMENT_CHEQUE = "cheque"
PAYMENT_TYPES = (PAYMENT_CASH, PAYMENT_ONLINE, PAYMENT_CHEQUE)
ENTERED_AMOUNT_TYPES = (PAYMENT_CASH, PAYMENT_CHEQUE)
# Both carry a reference number: a UTR for online, a cheque number for cheque.
REFERENCE_TYPES = (PAYMENT_ONLINE, PAYMENT_CHEQUE)

# A bill with hundreds of lines is a client bug, not a real sale, and each one
# costs a row.
MAX_BILL_ITEMS = 50


def _parse_items(
    raw_items: Optional[str],
    item_name: Optional[str],
    qty: Optional[int],
    rate: Optional[float],
) -> List[dict]:
    """
    Normalise the request's line items to `[{item_name, qty, rate}, ...]`.

    Accepts either the `items` JSON array (multi-item bills) or the original
    flat `item_name`/`qty`/`rate` fields. Clients written before multi-item
    bills send only the flat fields, so they keep working unchanged.
    """
    if raw_items:
        try:
            parsed: Any = json.loads(raw_items)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=422, detail="`items` must be a JSON array."
            )

        if not isinstance(parsed, list) or not parsed:
            raise HTTPException(
                status_code=422, detail="`items` must contain at least one item."
            )

        if len(parsed) > MAX_BILL_ITEMS:
            raise HTTPException(
                status_code=422,
                detail=f"A bill can hold at most {MAX_BILL_ITEMS} items.",
            )

        items = []
        for index, entry in enumerate(parsed):
            if not isinstance(entry, dict):
                raise HTTPException(
                    status_code=422,
                    detail=f"Item {index + 1} is not an object.",
                )

            name = str(entry.get("item_name") or "").strip()
            if not name:
                raise HTTPException(
                    status_code=422,
                    detail=f"Item {index + 1} is missing a name.",
                )

            try:
                entry_qty = int(entry.get("qty"))
                entry_rate = float(entry.get("rate"))
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=422,
                    detail=f"Item {index + 1} has an invalid quantity or rate.",
                )

            if entry_qty <= 0 or entry_rate <= 0:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Item {index + 1} needs a quantity and rate "
                        "greater than zero."
                    ),
                )

            items.append({"item_name": name, "qty": entry_qty, "rate": entry_rate})

        return items

    # Legacy single-item request.
    if not (item_name or "").strip():
        raise HTTPException(status_code=422, detail="Item name is required.")
    if qty is None or rate is None:
        raise HTTPException(
            status_code=422, detail="Quantity and rate are required."
        )
    if qty <= 0 or rate <= 0:
        raise HTTPException(
            status_code=422,
            detail="Quantity and rate must be greater than zero.",
        )

    return [{"item_name": item_name.strip(), "qty": qty, "rate": rate}]


# Registered on both "" and "/" so a multipart POST is never answered with a
# 307 redirect — some HTTP clients drop the body when replaying a redirect.
@router.post("", response_model=BillCreateResponse, status_code=201)
@router.post("/", response_model=BillCreateResponse, status_code=201,
             include_in_schema=False)
async def create_bill(
    phone: str = Form(...),
    customer_name: str = Form(""),
    # Multi-item bills send `items`; older clients send the three flat fields
    # below. Exactly one of the two forms has to be present — `_parse_items`
    # enforces that.
    items: Optional[str] = Form(None),
    item_name: Optional[str] = Form(None),
    qty: Optional[int] = Form(None),
    rate: Optional[float] = Form(None),
    payment_type: str = Form(...),
    amount_paid: Optional[float] = Form(None),
    transaction_number: Optional[str] = Form(None),
    transaction_screenshot: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a bill — finds or creates the customer, updates running totals."""
    # Previously unvalidated: any string was accepted and stored, which meant
    # a typo produced a bill the client could not classify.
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

    line_items = _parse_items(items, item_name, qty, rate)
    for line in line_items:
        line["line_total"] = round(line["qty"] * line["rate"], 2)
    bill_total = round(sum(line["line_total"] for line in line_items), 2)

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

    # What the customer owed before this sale. A payment larger than the bill
    # is normal — a customer settling old dues alongside a new purchase hands
    # over one amount covering both — so the excess is applied to that balance
    # rather than stored as a negative balance on this bill.
    outstanding_before = float(customer.total_unpaid or 0)

    if payment_type in ENTERED_AMOUNT_TYPES:
        paid = float(amount_paid or 0.0)

        # Beyond the bill *and* every outstanding due is a typo, not a
        # payment. Rejecting is more truthful than silently crediting a
        # balance the shop does not actually owe.
        payable = round(bill_total + outstanding_before, 2)
        if paid > payable:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Amount paid ({paid:.2f}) is more than this bill plus "
                    f"everything outstanding ({payable:.2f})."
                ),
            )

        unbalance = max(round(bill_total - paid, 2), 0.0)
        # Whatever the payment covered beyond this bill clears earlier dues.
        excess = max(round(paid - bill_total, 2), 0.0)
    else:
        # An online transfer settles the bill in full by definition.
        amount_paid = bill_total
        unbalance = 0.0
        excess = 0.0

    first = line_items[0]
    bill = Bill(
        customer_id=customer.id,
        # Flat columns mirror the first line so pre-existing readers of this
        # table keep seeing a usable item.
        item_name=first["item_name"],
        qty=first["qty"],
        rate=first["rate"],
        bill_total=bill_total,
        payment_type=payment_type,
        amount_paid=amount_paid,
        unbalance=unbalance,
        transaction_number=transaction_number,
        screenshot_data=screenshot_data,
        screenshot_mime=screenshot_mime,
    )
    bill.items = [
        BillItem(
            item_name=line["item_name"],
            qty=line["qty"],
            rate=line["rate"],
            line_total=line["line_total"],
            position=index,
        )
        for index, line in enumerate(line_items)
    ]
    db.add(bill)

    # Update customer running totals. Clamped at zero: the running balance is
    # what the customer owes, and it going negative would quietly corrupt
    # every screen that reads it.
    customer.total_amount = round(float(customer.total_amount or 0) + bill_total, 2)
    customer.total_unpaid = max(
        round(outstanding_before + unbalance - excess, 2), 0.0
    )

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
