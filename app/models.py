from typing import Optional

from sqlalchemy import (
    Column,
    Integer,
    String,
    LargeBinary,
    Numeric,
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class User(Base):
    """App users (shop owners) for authentication."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    phone = Column(String(20), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    # Letterhead shown on printed bills and PDFs. All optional — the owner's
    # account details are used as a fallback until these are filled in.
    business_name = Column(String(255), nullable=True)
    business_address = Column(Text, nullable=True)
    business_email = Column(String(255), nullable=True)
    business_phone = Column(String(40), nullable=True)
    business_alt_phone = Column(String(40), nullable=True)
    registration_number = Column(String(100), nullable=True)
    bill_footer_note = Column(Text, nullable=True)

    # Set the first time the owner saves their bill details. NULL means they
    # have not been through setup yet, so the app routes them there on login.
    onboarded_at = Column(DateTime, nullable=True)

    customers = relationship("Customer", back_populates="owner")
    products = relationship("Product", back_populates="owner")

    @property
    def onboarded(self) -> bool:
        """Exposed to the API so the app can gate its onboarding route."""
        return self.onboarded_at is not None


class PasswordResetCode(Base):
    """
    A short-lived code emailed to an owner who has forgotten their password.

    The code itself is never stored — only a hash — so a leaked database does
    not hand out working reset codes. Rows are kept after use rather than
    deleted, because `used_at` is what stops a code being replayed.
    """
    __tablename__ = "password_reset_codes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    code_hash = Column(String(255), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    # Wrong guesses, so a code can be locked out before it expires.
    attempts = Column(Integer, nullable=False, default=0)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    user = relationship("User")


class Customer(Base):
    """
    Billing customers, owned by one shop owner.

    Phone is unique *per owner*, not globally — two shops may legitimately
    bill the same person, and each keeps its own running totals.
    """
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("user_id", "phone", name="uq_customers_user_phone"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    phone = Column(String(20), nullable=False, index=True)
    total_amount = Column(Numeric(12, 2), nullable=False, server_default="0")
    total_unpaid = Column(Numeric(12, 2), nullable=False, server_default="0")
    created_at = Column(DateTime, server_default=func.now())

    owner = relationship("User", back_populates="customers")
    bills = relationship("Bill", back_populates="customer",
                         order_by="Bill.created_at.desc()")
    payments = relationship("Payment", back_populates="customer",
                            order_by="Payment.created_at.desc()",
                            cascade="all, delete-orphan")


class Bill(Base):
    """Individual bill linked to a customer."""
    __tablename__ = "bills"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    # First line item, mirrored from `items` below. A bill can now carry
    # several items, but these columns stay populated so rows written before
    # bill_items existed keep their meaning and clients that only read the
    # flat fields keep working.
    item_name = Column(String(255), nullable=False)
    qty = Column(Integer, nullable=False)
    rate = Column(Numeric(12, 2), nullable=False)
    bill_total = Column(Numeric(12, 2), nullable=False)
    payment_type = Column(String(10), nullable=False)
    amount_paid = Column(Numeric(12, 2), nullable=True)
    unbalance = Column(Numeric(12, 2), nullable=True)
    transaction_number = Column(String(255), nullable=True)
    # Legacy: path of a screenshot written to the local uploads directory.
    # Kept so pre-existing rows still resolve; new uploads go to the columns
    # below instead.
    transaction_screenshot_url = Column(String(500), nullable=True)

    # Payment screenshots live in the database rather than on disk: hosted
    # filesystems are ephemeral, so disk-backed images vanish on every deploy.
    # Storing the bytes here also lets the download be ownership-checked
    # instead of served from a public static mount.
    screenshot_data = Column(LargeBinary, nullable=True)
    screenshot_mime = Column(String(100), nullable=True)

    created_at = Column(DateTime, server_default=func.now())

    customer = relationship("Customer", back_populates="bills")
    items = relationship(
        "BillItem",
        back_populates="bill",
        order_by="BillItem.position",
        cascade="all, delete-orphan",
    )

    @property
    def screenshot_path(self) -> Optional[str]:
        """
        Where the client should fetch this bill's screenshot.

        Stored images resolve to an authenticated endpoint; legacy rows keep
        pointing at their old static path.

        Decided from the mime column (set exactly when the bytes are), never
        the blob itself — the blob is deferred on list queries, and touching
        it here would silently re-load megabytes per row.
        """
        if self.screenshot_mime is not None:
            return f"api/bills/{self.id}/screenshot"
        return self.transaction_screenshot_url


class BillItem(Base):
    """
    One line on a bill.

    Bills used to hold a single item in flat columns. Multi-item bills need a
    row per line, but the totals, payment and screenshot stay on the parent
    bill — splitting one sale across several bills would have shown up as
    several entries in the customer's history and forced the amount paid to be
    apportioned arbitrarily.
    """
    __tablename__ = "bill_items"

    id = Column(Integer, primary_key=True, index=True)
    bill_id = Column(
        Integer, ForeignKey("bills.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    item_name = Column(String(255), nullable=False)
    qty = Column(Integer, nullable=False)
    rate = Column(Numeric(12, 2), nullable=False)
    line_total = Column(Numeric(12, 2), nullable=False)
    # Preserves the order the items were entered in; without it the print
    # templates would re-order lines on every fetch.
    position = Column(Integer, nullable=False, server_default="0")

    bill = relationship("Bill", back_populates="items")


class Product(Base):
    """
    A barcoded item in one shop's catalogue.

    A barcode carries no name or price of its own, so the mapping has to be
    recorded once per shop. Scoped by owner and unique per barcode within that
    owner: two shops routinely stock the same product at different prices, and
    scoping the uniqueness by user is what stops one shop's catalogue from
    leaking into another's.
    """
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("user_id", "barcode", name="uq_products_user_barcode"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    barcode = Column(String(64), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    rate = Column(Numeric(12, 2), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    owner = relationship("User", back_populates="products")


class Payment(Base):
    """
    Money received against a customer's outstanding balance.

    Kept separate from Bill because it settles dues rather than recording a
    sale: it has no items and must not move the customer's billed total, only
    what they still owe. Folding it in as a zero-item bill would have
    corrupted every "total billed" figure in the app.
    """
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(
        Integer, ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    amount = Column(Numeric(12, 2), nullable=False)
    payment_type = Column(String(10), nullable=False)
    transaction_number = Column(String(255), nullable=True)
    note = Column(String(255), nullable=True)

    # Same reasoning as Bill: hosted filesystems are ephemeral, and the image
    # is a payment record that must stay behind an ownership check.
    screenshot_data = Column(LargeBinary, nullable=True)
    screenshot_mime = Column(String(100), nullable=True)

    created_at = Column(DateTime, server_default=func.now())

    customer = relationship("Customer", back_populates="payments")

    @property
    def screenshot_path(self) -> Optional[str]:
        """
        Authenticated download path, or None when no image was attached.

        Keyed off the mime column so the deferred blob is never touched.
        """
        if self.screenshot_mime is not None:
            return f"api/payments/{self.id}/screenshot"
        return None
