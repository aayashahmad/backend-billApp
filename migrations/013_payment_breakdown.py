"""
Each payment records what the money did.

A payment can clear a debt, become credit, or both. A receipt reprinted later
must show what happened when the money changed hands rather than today's
balances — the same reason bills carry their own settlement.

Idempotent: safe to run more than once.

    python -m migrations.013_payment_breakdown
"""
from sqlalchemy import text

from app.database import engine

COLUMNS = (
    "applied_to_dues",
    "advance_added",
    "outstanding_after",
    "advance_balance_after",
)


def migrate() -> None:
    with engine.begin() as conn:
        existing = {
            row[0]
            for row in conn.execute(
                text(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'payments'
                    """
                )
            )
        }

        for column in COLUMNS:
            if column in existing:
                print(f"· payments.{column} already present")
                continue
            print(f"· adding payments.{column}")
            conn.execute(
                text(
                    f"ALTER TABLE payments ADD COLUMN {column} "
                    "NUMERIC(12, 2) NOT NULL DEFAULT 0"
                )
            )

    print("✓ migration complete")


if __name__ == "__main__":
    migrate()
