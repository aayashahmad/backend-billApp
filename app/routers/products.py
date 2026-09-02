from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Product, User
from app.schemas import (
    ProductCreate,
    ProductImportRequest,
    ProductImportResult,
    ProductOut,
    ProductUpdate,
)

router = APIRouter(prefix="/api/products", tags=["products"])

# One request holds the whole file, so this bounds both the payload and the
# work done inside a single transaction.
MAX_IMPORT_ROWS = 5000


def _owned(db: Session, user: User):
    """Base query restricted to the signed-in owner's catalogue."""
    return db.query(Product).filter(Product.user_id == user.id)


def _normalise_barcode(barcode: str) -> str:
    return (barcode or "").strip()


@router.get("", response_model=List[ProductOut])
@router.get("/", response_model=List[ProductOut], include_in_schema=False)
def list_products(
    q: Optional[str] = Query(None, description="Match on name or barcode"),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The owner's catalogue, alphabetically, optionally filtered."""
    query = _owned(db, current_user)

    if q and q.strip():
        pattern = f"%{q.strip()}%"
        query = query.filter(
            or_(Product.name.ilike(pattern), Product.barcode.ilike(pattern))
        )

    return query.order_by(Product.name.asc()).offset(offset).limit(limit).all()


@router.get("/by-barcode/{barcode}", response_model=ProductOut)
def get_product_by_barcode(
    barcode: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Resolve a scanned barcode to this owner's product.

    A 404 means "not in this shop's catalogue yet" — the app treats that as a
    new product and offers to save it. Another owner's product with the same
    barcode must not resolve here, which the scoped query already ensures.
    """
    product = (
        _owned(db, current_user)
        .filter(Product.barcode == _normalise_barcode(barcode))
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.post("", response_model=ProductOut, status_code=201)
@router.post("/", response_model=ProductOut, status_code=201, include_in_schema=False)
def upsert_product(
    payload: ProductCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create the product, or update it if the barcode is already known.

    Upsert rather than 409 on purpose: the bill form saves a product as a
    side effect of billing it, so a re-scan at a new price should record the
    new price instead of failing a sale the user has already completed.
    """
    barcode = _normalise_barcode(payload.barcode)
    if not barcode:
        raise HTTPException(status_code=422, detail="Barcode is required.")

    product = _owned(db, current_user).filter(Product.barcode == barcode).first()

    if product:
        product.name = payload.name.strip()
        product.rate = payload.rate
    else:
        product = Product(
            user_id=current_user.id,
            barcode=barcode,
            name=payload.name.strip(),
            rate=payload.rate,
        )
        db.add(product)

    db.commit()
    db.refresh(product)
    return product


# Registered before "/{product_id}" so the literal path is not captured by it.
@router.post("/import", response_model=ProductImportResult)
def import_products(
    payload: ProductImportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Bulk upsert a catalogue.

    Stocking a shop one item at a time is impractical, so this takes a whole
    price list in one request. Rows are upserted on barcode like a single
    create, and a bad row is reported by position rather than failing the
    whole import — one typo in a long list should not cost the other rows.
    """
    if not payload.rows:
        raise HTTPException(status_code=422, detail="No rows to import.")

    if len(payload.rows) > MAX_IMPORT_ROWS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{len(payload.rows)} rows is more than the "
                f"{MAX_IMPORT_ROWS} allowed in one import."
            ),
        )

    existing = {
        product.barcode: product for product in _owned(db, current_user).all()
    }

    result = ProductImportResult()
    # A barcode repeated within one file would otherwise be counted twice.
    seen: dict[str, Product] = {}

    for index, row in enumerate(payload.rows, start=1):
        barcode = _normalise_barcode(row.barcode)
        if not barcode:
            result.errors.append(f"Row {index}: missing barcode.")
            continue

        product = seen.get(barcode) or existing.get(barcode)

        if product is None:
            product = Product(
                user_id=current_user.id,
                barcode=barcode,
                name=row.name.strip(),
                rate=row.rate,
            )
            db.add(product)
            result.created += 1
        else:
            product.name = row.name.strip()
            product.rate = row.rate
            # Only count an update once, however often the barcode repeats.
            if barcode not in seen:
                result.updated += 1

        seen[barcode] = product

    db.commit()
    return result


@router.put("/{product_id}", response_model=ProductOut)
def update_product(
    product_id: int,
    payload: ProductUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Edit a catalogue entry. The barcode itself is immutable — a different
    barcode is a different product, so it is added rather than renamed."""
    product = _owned(db, current_user).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    if payload.name is not None:
        product.name = payload.name.strip()
    if payload.rate is not None:
        product.rate = payload.rate

    db.commit()
    db.refresh(product)
    return product


@router.delete("/{product_id}", status_code=204)
def delete_product(
    product_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Remove a catalogue entry.

    Bills are unaffected: a bill stores the item name and rate it was written
    with, so deleting the product never rewrites history.
    """
    product = _owned(db, current_user).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    db.delete(product)
    db.commit()
