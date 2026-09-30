"""
Opening balances carried over from a shop's paper khata.

Kept on the customer rather than recorded as a bill: an opening balance is
not a sale, and counting it as one would inflate every takings and profit
figure the shop ever sees — permanently, and invisibly.

Idempotent: safe to run more than once.

    python -m migrations.016_opening_balance
"""
from sqlalchemy import text

from app.database import engine

COLUMNS = {
    "opening_balance": "NUMERIC(12, 2)",
    "opening_balance_type": "VARCHAR(10)",
    "opening_balance_date": "TIMESTAMP",
    "opening_balance_ref": "VARCHAR(100)",
    "opening_balance_note": "TEXT",
    "opening_balance_set_at": "TIMESTAMP",
}


def migrate() -> None:
    with engine.begin() as conn:
        present = {
            row[0]
            for row in conn.execute(
                text(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'customers'
                    """
                )
            )
        }

        for name, ddl in COLUMNS.items():
            if name in present:
                print(f"· customers.{name} already present")
                continue
            print(f"· adding customers.{name}")
            conn.execute(text(f"ALTER TABLE customers ADD COLUMN {name} {ddl}"))

    print("✓ migration complete")


if __name__ == "__main__":
    migrate()
