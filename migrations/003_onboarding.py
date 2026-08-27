"""
Track whether an owner has completed bill-details setup.

`onboarded_at` is NULL until the owner saves their bill details for the first
time; the app routes anyone with NULL to the setup screen on login.

Backfill rule: owners who already customised something beyond the values
migration 002 seeded from their account (an address, a registration number or
a footer note) are treated as already set up. Owners carrying only the seeded
defaults are not, so they get the setup screen once.

Idempotent: safe to run more than once.

    python -m migrations.003_onboarding
"""
from sqlalchemy import text

from app.database import engine


def migrate() -> None:
    with engine.begin() as conn:
        exists = conn.execute(
            text(
                """
                SELECT count(*) FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = 'onboarded_at'
                """
            )
        ).scalar()

        if exists:
            print("· users.onboarded_at already present")
        else:
            print("· adding users.onboarded_at")
            conn.execute(text("ALTER TABLE users ADD COLUMN onboarded_at TIMESTAMP"))

            marked = conn.execute(
                text(
                    """
                    UPDATE users
                    SET onboarded_at = COALESCE(created_at, now())
                    WHERE onboarded_at IS NULL
                      AND (
                        business_address    IS NOT NULL
                        OR registration_number IS NOT NULL
                        OR bill_footer_note    IS NOT NULL
                      )
                    """
                )
            ).rowcount
            print(f"· marked {marked} already-customised owner(s) as set up")

        pending = conn.execute(
            text("SELECT count(*) FROM users WHERE onboarded_at IS NULL")
        ).scalar()
        print(f"· {pending} owner(s) will see the setup screen on next login")

    print("✓ migration complete")


if __name__ == "__main__":
    migrate()
