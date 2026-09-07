"""
Per-customer credit limit — how much a customer may owe at once.

Shops extend credit by reputation, and the ceiling differs per customer. The
column is nullable on purpose: NULL means no limit at all, which is a
different statement from 0, meaning "cash only, no credit". Collapsing the
two would silently put every customer on a zero limit.

Idempotent: safe to run more than once.

    python -m migrations.010_customer_credit_limit
"""
from sqlalchemy import text

from app.database import engine


def migrate() -> None:
    with engine.begin() as conn:
        exists = conn.execute(
            text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'customers' AND column_name = 'credit_limit'
                """
            )
        ).first()

        if exists:
            print("· customers.credit_limit already present")
        else:
            print("· adding customers.credit_limit")
            conn.execute(
                text("ALTER TABLE customers ADD COLUMN credit_limit NUMERIC(12, 2)")
            )

    print("✓ migration complete")


if __name__ == "__main__":
    migrate()
