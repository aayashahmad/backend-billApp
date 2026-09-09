from datetime import datetime
from typing import Optional, List

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


# ── Auth ─────────────────────────────────────────────────────────────

class SignupRequest(BaseModel):
    username: str
    email: str
    phone: str
    # The app enforces 6 too; without this floor a raw API call could
    # register an account with an empty password.
    password: str = Field(min_length=6)


class LoginRequest(BaseModel):
    login: str  # email or phone
    password: str


class AuthResponse(BaseModel):
    token: str
    username: str


class ForgotPasswordRequest(BaseModel):
    """Reset always starts from the phone number — what an owner remembers."""
    phone: str


class ForgotPasswordResponse(BaseModel):
    """
    Deliberately says the same thing whether or not the number is registered.

    Naming the account would turn this endpoint into a way to test which
    phone numbers have an account, so the copy is generic and `email_hint`
    stays null on the miss.
    """
    message: str
    email_hint: Optional[str] = None


class ResetPasswordRequest(BaseModel):
    phone: str
    code: str
    new_password: str = Field(min_length=6)


class EmailVerificationRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)


class EmailVerificationConfirm(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    code: str


class EmailVerificationResponse(BaseModel):
    message: str
    # Minutes the code stays valid, so the screen can say so without
    # hardcoding a number that might drift from the server's.
    expires_in_minutes: int = 15


class BusinessProfileOut(BaseModel):
    """Letterhead printed on bills and PDFs."""
    business_name: Optional[str] = None
    business_address: Optional[str] = None
    business_email: Optional[str] = None
    business_phone: Optional[str] = None
    business_alt_phone: Optional[str] = None
    registration_number: Optional[str] = None
    # Printed on the bill so a customer can pay or get in touch without
    # asking: tax identity, the two ways to send money, and a chat number.
    gstin: Optional[str] = None
    upi_id: Optional[str] = None
    bank_account_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_ifsc: Optional[str] = None
    whatsapp_number: Optional[str] = None
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
    gstin: Optional[str] = None
    upi_id: Optional[str] = None
    bank_account_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_ifsc: Optional[str] = None
    whatsapp_number: Optional[str] = None
    bill_footer_note: Optional[str] = None


class AccountUpdate(BaseModel):
    """
    The owner's own account details.

    `current_password` is required only when the email or phone changes:
    both are credentials — email receives password-reset codes and phone is a
    login identifier — so changing them from a stolen session would be an
    account takeover. Renaming yourself is harmless and stays frictionless.
    """
    username: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=255)
    phone: str = Field(min_length=7, max_length=20)
    current_password: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    """
    Changing your password while signed in.

    Separate from the reset flow on purpose: someone who still knows their
    password should not have to go near email to change it, which also means
    the routine case keeps working if mail delivery is ever down.
    """
    current_password: str
    new_password: str = Field(min_length=6)


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

class CustomerUpdate(BaseModel):
    """
    Editable settings on a customer.

    `credit_limit` is optional and nullable, and the two mean different
    things: omitted leaves the limit alone, explicit null removes it.
    """
    credit_limit: Optional[float] = Field(default=None, ge=0)
    # Distinguishes "clear the limit" from "leave it as it is", which a bare
    # null cannot express on its own.
    clear_credit_limit: bool = False


class CustomerOut(BaseModel):
    id: int
    name: str
    phone: str
    credit_limit: Optional[float] = None
    total_amount: float
    total_unpaid: float
    # Paid ahead and not yet used. Applied automatically to the next bill.
    advance_balance: float = 0

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
    # Money actually received at the counter for this bill.
    amount_paid: Optional[float] = None
    # What is still owed on this bill.
    unbalance: Optional[float] = None
    # The settlement, recorded so every document reads the same figures
    # rather than recomputing them and drifting apart.
    advance_applied: float = 0
    advance_added: float = 0
    advance_balance_after: float = 0
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
    # What the money did: how much cleared a debt, how much became credit,
    # and where the account stood afterwards.
    applied_to_dues: float = 0
    advance_added: float = 0
    outstanding_after: float = 0
    advance_balance_after: float = 0
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


class ProductImportRow(ProductBase):
    """One row of an import. Same rules as a single create."""


class ProductImportRequest(BaseModel):
    rows: List[ProductImportRow]


class ProductImportResult(BaseModel):
    created: int = 0
    updated: int = 0
    # Rows the server refused, each with its 1-based position so the owner can
    # find it in their spreadsheet rather than guess.
    errors: List[str] = []


# ── Reports ──────────────────────────────────────────────────────────

class ReportBucket(BaseModel):
    """One column of the chart: a day, week, month or year."""
    label: str
    starts_at: datetime
    bills: int
    # What was sold in this bucket.
    billed: float
    # Money that arrived in it — counter payments plus later settlements of
    # old dues, which is why it can exceed `billed`.
    collected: float
    # What this bucket added to the book, never negative.
    outstanding: float


class ReportTotals(BaseModel):
    bills: int
    billed: float
    collected: float
    outstanding: float


class ReportSummary(BaseModel):
    period: str
    buckets: List[ReportBucket] = []
    totals: ReportTotals
