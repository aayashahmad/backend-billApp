"""
Payment and tax details printed on every bill.

A customer holding a bill should be able to pay it without asking how: a UPI
QR to scan, bank details to transfer to, and a number to message. GSTIN is
stored separately from `registration_number` because a shop can hold a trade
licence without being GST-registered, and only a GSTIN may be presented as
one on a tax invoice.

Idempotent: safe to run more than once.

    python -m migrations.009_bill_payment_details
"""
from sqlalchemy import text

from app.database import engine

COLUMNS = {
    "gstin": "VARCHAR(20)",
    "upi_id": "VARCHAR(120)",
    "bank_account_name": "VARCHAR(150)",
    "bank_account_number": "VARCHAR(40)",
    "bank_ifsc": "VARCHAR(15)",
    "whatsapp_number": "VARCHAR(20)",
}


def migrate() -> None:
    with engine.begin() as conn:
        existing = {
            row[0]
            for row in conn.execute(
                text(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'users'
                    """
                )
            )
        }

        for column, ddl_type in COLUMNS.items():
            if column in existing:
                print(f"· users.{column} already present")
                continue
            print(f"· adding users.{column}")
            conn.execute(text(f"ALTER TABLE users ADD COLUMN {column} {ddl_type}"))

    print("✓ migration complete")


if __name__ == "__main__":
    migrate()
