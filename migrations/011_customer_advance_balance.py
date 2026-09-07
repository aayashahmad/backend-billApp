"""
Advance balance — money a customer has paid ahead.

Customers routinely hand over a round figure, or pay before a festival. That
surplus used to be refused; it is now held here and applied to their next
bill.

Kept as its own positive column rather than a negative `total_unpaid`: every
screen and printed document reads "unpaid" as a debt, and a negative one
would render as a balance the shop owes.

Idempotent: safe to run more than once.

    python -m migrations.011_customer_advance_balance
"""
from sqlalchemy import text

from app.database import engine


def migrate() -> None:
    with engine.begin() as conn:
        exists = conn.execute(
            text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'customers' AND column_name = 'advance_balance'
                """
            )
        ).first()

        if exists:
            print("· customers.advance_balance already present")
        else:
            print("· adding customers.advance_balance")
            conn.execute(
                text(
                    "ALTER TABLE customers ADD COLUMN advance_balance "
                    "NUMERIC(12, 2) NOT NULL DEFAULT 0"
                )
            )

    print("✓ migration complete")


if __name__ == "__main__":
    migrate()
