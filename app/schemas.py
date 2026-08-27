from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel


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

class BillOut(BaseModel):
    id: int
    customer_id: int
    item_name: str
    qty: int
    rate: float
    bill_total: float
    payment_type: str
    amount_paid: Optional[float] = None
    unbalance: Optional[float] = None
    transaction_number: Optional[str] = None
    transaction_screenshot_url: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class CustomerWithBills(CustomerOut):
    bills: List[BillOut] = []


class BillCreateResponse(BaseModel):
    bill: BillOut
    customer: CustomerOut
