"""
One-time codes proving somebody controls an email address at signup.

Keyed by address rather than by user: at the moment the code is sent there is
no account yet. Only the hash is stored, for the same reason as the password
reset codes — a leaked table must not hand out working codes.

Idempotent: safe to run more than once.

    python -m migrations.014_email_verifications
"""
from sqlalchemy import text

from app.database import engine

CREATE_TABLE = """
CREATE TABLE email_verifications (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    code_hash VARCHAR(255) NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    verified_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
)
"""

CREATE_INDEX = (
    "CREATE INDEX ix_email_verifications_email ON email_verifications (email)"
)


def migrate() -> None:
    with engine.begin() as conn:
        exists = conn.execute(
            text(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'email_verifications'
                """
            )
        ).first()

        if exists:
            print("· email_verifications already present")
        else:
            print("· creating email_verifications")
            conn.execute(text(CREATE_TABLE))
            conn.execute(text(CREATE_INDEX))

    print("✓ migration complete")


if __name__ == "__main__":
    migrate()
