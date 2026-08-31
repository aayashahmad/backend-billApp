"""
Give bills line items.

A bill used to hold exactly one item in flat columns, so a sale of several
things had to be recorded as several bills — which showed up as several
entries in the customer's history and forced the amount paid to be split
across them arbitrarily. `bill_items` holds a row per line while the totals,
payment and screenshot stay on the parent bill.

`bills.item_name`/`qty`/`rate` are left in place and keep mirroring the first
line, so rows written before this migration keep their meaning and any reader
of the flat columns keeps working.

Backfills one bill_items row per existing bill from those flat columns.

Idempotent: safe to run more than once.

    python -m migrations.005_bill_items
"""
from sqlalchemy import text

from app.database import engine

CREATE_TABLE = """
CREATE TABLE bill_items (
    id SERIAL PRIMARY KEY,
    bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    item_name VARCHAR(255) NOT NULL,
    qty INTEGER NOT NULL,
    rate NUMERIC(12, 2) NOT NULL,
    line_total NUMERIC(12, 2) NOT NULL,
    position INTEGER NOT NULL DEFAULT 0
)
"""

CREATE_INDEX = "CREATE INDEX ix_bill_items_bill_id ON bill_items (bill_id)"

# Only bills that have no lines yet, so re-running never duplicates them.
BACKFILL = """
INSERT INTO bill_items (bill_id, item_name, qty, rate, line_total, position)
SELECT b.id, b.item_name, b.qty, b.rate, ROUND(b.qty * b.rate, 2), 0
FROM bills b
WHERE NOT EXISTS (SELECT 1 FROM bill_items i WHERE i.bill_id = b.id)
"""


def migrate() -> None:
    with engine.begin() as conn:
        exists = conn.execute(
            text(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'bill_items'
                """
            )
        ).first()

        if exists:
            print("· bill_items already present")
        else:
            print("· creating bill_items")
            conn.execute(text(CREATE_TABLE))
            conn.execute(text(CREATE_INDEX))

        result = conn.execute(text(BACKFILL))
        backfilled = result.rowcount or 0
        if backfilled:
            print(f"· backfilled {backfilled} bill(s) with a single line item")
        else:
            print("· no bills needed backfilling")

    print("✓ migration complete")


if __name__ == "__main__":
    migrate()
