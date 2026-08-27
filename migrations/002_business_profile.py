"""
Add the printable business letterhead to users.

Every column is nullable, so no backfill is needed — an owner who has not
filled these in falls back to their account name/email/phone on documents.

Idempotent: safe to run more than once.

    python -m migrations.002_business_profile
"""
from sqlalchemy import text

from app.database import engine

COLUMNS = {
    "business_name": "VARCHAR(255)",
    "business_address": "TEXT",
    "business_email": "VARCHAR(255)",
    "business_phone": "VARCHAR(40)",
    "business_alt_phone": "VARCHAR(40)",
    "registration_number": "VARCHAR(100)",
    "bill_footer_note": "TEXT",
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

        for column, column_type in COLUMNS.items():
            if column in existing:
                print(f"· users.{column} already present")
                continue
            print(f"· adding users.{column}")
            conn.execute(
                text(f"ALTER TABLE users ADD COLUMN {column} {column_type}")
            )

        # Seed the letterhead from the account details so documents look
        # right immediately, before the owner opens the settings screen.
        seeded = conn.execute(
            text(
                """
                UPDATE users
                SET business_name  = COALESCE(business_name, username),
                    business_email = COALESCE(business_email, email),
                    business_phone = COALESCE(business_phone, phone)
                WHERE business_name IS NULL
                   OR business_email IS NULL
                   OR business_phone IS NULL
                """
            )
        ).rowcount
        if seeded:
            print(f"· seeded letterhead defaults for {seeded} owner(s)")

    print("✓ migration complete")


if __name__ == "__main__":
    migrate()
