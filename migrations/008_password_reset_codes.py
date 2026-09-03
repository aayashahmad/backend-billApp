"""
One-time codes for the forgot-password flow.

An owner who forgets their password has no way back in without this: the app
has no admin console, and support cannot verify who is asking. The code is
emailed to the address on the account, so only the hash is stored here — a
leaked table must not hand out working codes.

Idempotent: safe to run more than once.

    python -m migrations.008_password_reset_codes
"""
from sqlalchemy import text

from app.database import engine

CREATE_TABLE = """
CREATE TABLE password_reset_codes (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash VARCHAR(255) NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
)
"""

CREATE_INDEX = (
    "CREATE INDEX ix_password_reset_codes_user_id "
    "ON password_reset_codes (user_id)"
)


def migrate() -> None:
    with engine.begin() as conn:
        exists = conn.execute(
            text(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'password_reset_codes'
                """
            )
        ).first()

        if exists:
            print("· password_reset_codes already present")
        else:
            print("· creating password_reset_codes")
            conn.execute(text(CREATE_TABLE))
            conn.execute(text(CREATE_INDEX))

    print("✓ migration complete")


if __name__ == "__main__":
    migrate()
