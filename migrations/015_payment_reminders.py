"""
Automatic payment reminders.

Adds where a reminder can be sent (the customer's email), the rules deciding
who gets one, and the master switch on the shop. The switch defaults to FALSE
on purpose: turning this migration on must never start messaging a shop's
customers on its own.

Idempotent: safe to run more than once.

    python -m migrations.015_payment_reminders
"""
from sqlalchemy import text

from app.database import engine

CUSTOMER_COLUMNS = {
    "email": "VARCHAR(255)",
    "reminder_enabled": "BOOLEAN NOT NULL DEFAULT TRUE",
    "reminder_min_amount": "NUMERIC(12, 2)",
    "reminder_after_days": "INTEGER",
    "last_reminded_at": "TIMESTAMP",
}

USER_COLUMNS = {
    "reminders_enabled": "BOOLEAN NOT NULL DEFAULT FALSE",
    "reminder_min_amount": "NUMERIC(12, 2) NOT NULL DEFAULT 100",
    "reminder_after_days": "INTEGER NOT NULL DEFAULT 7",
    "expo_push_token": "VARCHAR(255)",
}


def _add_missing(conn, table, columns):
    present = {
        row[0]
        for row in conn.execute(
            text(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = :table
                """
            ),
            {"table": table},
        )
    }

    for name, ddl in columns.items():
        if name in present:
            print(f"· {table}.{name} already present")
            continue
        print(f"· adding {table}.{name}")
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def migrate() -> None:
    with engine.begin() as conn:
        _add_missing(conn, "customers", CUSTOMER_COLUMNS)
        _add_missing(conn, "users", USER_COLUMNS)

    print("✓ migration complete")


if __name__ == "__main__":
    migrate()
