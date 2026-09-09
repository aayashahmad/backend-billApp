"""
Shared helpers for the API tests.

Signup now insists the email address has been proved, which a test cannot do
through the API: the code is emailed and only its hash is stored. So tests
write the proof straight into the table, which is exactly the state a real
signup reaches after the owner types the code from their inbox.
"""

from datetime import datetime, timedelta


def mark_email_verified(email: str) -> None:
    """Puts an address in the state a completed email verification leaves it."""
    # Imported here, not at module scope: conftest loads before the test
    # modules, and importing the app would read the environment before each
    # suite has finished setting DATABASE_URL and blanking mail credentials.
    from app.database import SessionLocal
    from app.models import EmailVerification

    address = (email or "").strip().lower()
    session = SessionLocal()
    try:
        session.query(EmailVerification).filter(
            EmailVerification.email == address
        ).delete(synchronize_session=False)
        session.add(
            EmailVerification(
                email=address,
                code_hash="already-verified",
                expires_at=datetime.utcnow() + timedelta(minutes=15),
                attempts=0,
                verified_at=datetime.utcnow(),
            )
        )
        session.commit()
    finally:
        session.close()
