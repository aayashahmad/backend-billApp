"""
Record payments received against a customer's outstanding balance.

Customers routinely settle old dues without buying anything, and there was no
way to record that: the only way to move `total_unpaid` was to write a bill,
which would have inflated the customer's billed total by money they were
paying back rather than spending.

`payments` is separate from `bills` for that reason — it moves `total_unpaid`
only and never `total_amount`.

Idempotent: safe to run more than once.

    python -m migrations.007_payments
"""
from sqlalchemy import text

from app.database import engine

CREATE_TABLE = """
CREATE TABLE payments (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    amount NUMERIC(12, 2) NOT NULL,
    payment_type VARCHAR(10) NOT NULL,
    transaction_number VARCHAR(255),
    note VARCHAR(255),
    screenshot_data BYTEA,
    screenshot_mime VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
)
"""

CREATE_INDEX = "CREATE INDEX ix_payments_customer_id ON payments (customer_id)"


def migrate() -> None:
    with engine.begin() as conn:
        exists = conn.execute(
            text(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'payments'
                """
            )
        ).first()

        if exists:
            print("· payments already present")
        else:
            print("· creating payments")
            conn.execute(text(CREATE_TABLE))
            conn.execute(text(CREATE_INDEX))

    print("✓ migration complete")


if __name__ == "__main__":
    migrate()
