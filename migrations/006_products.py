"""
Add the per-shop product catalogue.

A scanned barcode is only a number — nothing in it carries the item's name or
price. `products` records that mapping once per shop so a later scan of the
same code fills the bill line in automatically.

Scoped by owner, and unique on (user_id, barcode) rather than barcode alone:
two shops routinely stock the same product at different prices, and a global
unique constraint would let the first shop to scan a code claim it for
everyone.

Idempotent: safe to run more than once.

    python -m migrations.006_products
"""
from sqlalchemy import text

from app.database import engine

CREATE_TABLE = """
CREATE TABLE products (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    barcode VARCHAR(64) NOT NULL,
    name VARCHAR(255) NOT NULL,
    rate NUMERIC(12, 2) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    CONSTRAINT uq_products_user_barcode UNIQUE (user_id, barcode)
)
"""

INDEXES = (
    "CREATE INDEX ix_products_user_id ON products (user_id)",
    "CREATE INDEX ix_products_barcode ON products (barcode)",
)


def migrate() -> None:
    with engine.begin() as conn:
        exists = conn.execute(
            text(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'products'
                """
            )
        ).first()

        if exists:
            print("· products already present")
        else:
            print("· creating products")
            conn.execute(text(CREATE_TABLE))
            for statement in INDEXES:
                conn.execute(text(statement))

    print("✓ migration complete")


if __name__ == "__main__":
    migrate()
