"""
Each bill records how it was settled.

A receipt reprinted next month must show what happened when it was written,
not what the customer's balance looks like today — so the credit a bill drew
down, the credit it created, and the balance it left behind are stored on the
bill rather than derived from the customer afterwards.

Idempotent: safe to run more than once.

    python -m migrations.012_bill_settlement_breakdown
"""
from sqlalchemy import text

from app.database import engine

COLUMNS = ("advance_applied", "advance_added", "advance_balance_after")


def migrate() -> None:
    with engine.begin() as conn:
        existing = {
            row[0]
            for row in conn.execute(
                text(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'bills'
                    """
                )
            )
        }

        for column in COLUMNS:
            if column in existing:
                print(f"· bills.{column} already present")
                continue
            print(f"· adding bills.{column}")
            conn.execute(
                text(
                    f"ALTER TABLE bills ADD COLUMN {column} "
                    "NUMERIC(12, 2) NOT NULL DEFAULT 0"
                )
            )

    print("✓ migration complete")


if __name__ == "__main__":
    migrate()
