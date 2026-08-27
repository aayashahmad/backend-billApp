from sqlalchemy import (
    Column,
    Integer,
    String,
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

    @property
    def onboarded(self) -> bool:
        """Exposed to the API so the app can gate its onboarding route."""
        return self.onboarded_at is not None


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


class Bill(Base):
    """Individual bill linked to a customer."""
    __tablename__ = "bills"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    item_name = Column(String(255), nullable=False)
    qty = Column(Integer, nullable=False)
    rate = Column(Numeric(12, 2), nullable=False)
    bill_total = Column(Numeric(12, 2), nullable=False)
    payment_type = Column(String(10), nullable=False)
    amount_paid = Column(Numeric(12, 2), nullable=True)
    unbalance = Column(Numeric(12, 2), nullable=True)
    transaction_number = Column(String(255), nullable=True)
    transaction_screenshot_url = Column(String(500), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    customer = relationship("Customer", back_populates="bills")
