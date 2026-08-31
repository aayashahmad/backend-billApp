from datetime import datetime
from typing import Optional, List

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


# ── Auth ─────────────────────────────────────────────────────────────

class SignupRequest(BaseModel):
    username: str
    email: str
    phone: str
    password: str


class LoginRequest(BaseModel):
    login: str  # email or phone
    password: str


class AuthResponse(BaseModel):
    token: str
    username: str


class BusinessProfileOut(BaseModel):
    """Letterhead printed on bills and PDFs."""
    business_name: Optional[str] = None
    business_address: Optional[str] = None
    business_email: Optional[str] = None
    business_phone: Optional[str] = None
    business_alt_phone: Optional[str] = None
    registration_number: Optional[str] = None
    bill_footer_note: Optional[str] = None
    onboarded: bool = False

    class Config:
        from_attributes = True


class BusinessProfileUpdate(BaseModel):
    """
    All fields optional — the screen submits the whole form, and an empty
    string clears a field rather than leaving the previous value behind.
    """
    business_name: Optional[str] = None
    business_address: Optional[str] = None
    business_email: Optional[str] = None
    business_phone: Optional[str] = None
    business_alt_phone: Optional[str] = None
    registration_number: Optional[str] = None
    bill_footer_note: Optional[str] = None


class UserProfileOut(BusinessProfileOut):
    """Signed-in shop owner's own profile, including their letterhead."""
    id: int
    username: str
    email: str
    phone: str
    created_at: datetime
    # False until bill details are saved once — the app routes new owners to
    # the setup screen while this is False.
    onboarded: bool = False

    class Config:
        from_attributes = True


# ── Customer ─────────────────────────────────────────────────────────

class CustomerOut(BaseModel):
    id: int
    name: str
    phone: str
    total_amount: float
    total_unpaid: float

    class Config:
        from_attributes = True


# ── Bill ─────────────────────────────────────────────────────────────

class BillItemOut(BaseModel):
    id: int
    item_name: str
    qty: int
    rate: float
    line_total: float
    position: int

    model_config = ConfigDict(from_attributes=True)


class BillOut(BaseModel):
    id: int
    customer_id: int
    # The first line item, kept flat for clients written before multi-item
    # bills existed. `items` below is the full list.
    item_name: str
    qty: int
    rate: float
    bill_total: float
    payment_type: str
    amount_paid: Optional[float] = None
    unbalance: Optional[float] = None
    transaction_number: Optional[str] = None
    # Sourced from Bill.screenshot_path, which points at the authenticated
    # download endpoint for stored images and falls back to the legacy static
    # path for rows predating database storage. The field keeps its original
    # name so existing clients need no change.
    transaction_screenshot_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "screenshot_path", "transaction_screenshot_url"
        ),
    )
    created_at: datetime
    items: List[BillItemOut] = []

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class PaymentOut(BaseModel):
    id: int
    customer_id: int
    amount: float
    payment_type: str
    transaction_number: Optional[str] = None
    note: Optional[str] = None
    # Mirrors BillOut: sourced from Payment.screenshot_path, which points at
    # the ownership-checked download endpoint.
    transaction_screenshot_url: Optional[str] = Field(
        default=None, validation_alias=AliasChoices("screenshot_path"),
    )
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class CustomerWithBills(CustomerOut):
    bills: List[BillOut] = []
    payments: List[PaymentOut] = []


class BillCreateResponse(BaseModel):
    bill: BillOut
    customer: CustomerOut


# ── Product ──────────────────────────────────────────────────────────

class ProductBase(BaseModel):
    barcode: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    rate: float = Field(gt=0)


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    """Every field optional — the products screen sends only what changed."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    rate: Optional[float] = Field(default=None, gt=0)


class ProductOut(ProductBase):
    id: int

    model_config = ConfigDict(from_attributes=True)
