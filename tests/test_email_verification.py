"""
Signup email verification.

The email on an account is the only channel a forgotten password travels
back through, so a typo taken on trust locks a shopkeeper out of their own
books permanently. These tests pin down that the address is proved before an
account exists, and that the proof cannot be guessed or replayed.
"""

import os
import re
import tempfile

os.environ.setdefault("DATABASE_URL", "sqlite:///" + tempfile.mktemp(suffix=".db"))
os.environ.pop("SECRET_KEY", None)

# Blank every mail credential BEFORE the app is imported, so the suite falls
# back to the dev log instead of sending live email to a real inbox.
for _var in (
    "BREVO_API_KEY",
    "MAIL_FROM",
    "SMTP_HOST",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_FROM",
):
    os.environ[_var] = ""

from fastapi.testclient import TestClient

from app.main import app
from app.routers import auth as auth_router

client = TestClient(app)

auth_router.VERIFICATION_COOLDOWN_SECONDS = 0  # no waiting between test cases


def _request_code(caplog, email):
    """Ask for a code and read it back out of the dev log."""
    with caplog.at_level("WARNING"):
        caplog.clear()
        response = client.post(
            "/api/auth/verify-email/request", json={"email": email}
        )
    match = re.search(r"verification code for \S+: (\d{6})", caplog.text)
    return response, (match.group(1) if match else None)


def _signup(email, phone, password="verifypass1"):
    return client.post(
        "/api/auth/signup",
        json={
            "username": "Verified Shop",
            "email": email,
            "phone": phone,
            "password": password,
        },
    )


def test_signup_is_refused_until_the_address_is_proved():
    response = _signup("unproved@shop.test", "9440000001")
    assert response.status_code == 403, response.text
    assert "Verify your email" in response.json()["detail"]


def test_the_full_journey_from_code_to_account(caplog):
    email = "journey@shop.test"
    response, code = _request_code(caplog, email)
    assert response.status_code == 200, response.text
    assert code, "no code reached the dev log"

    confirmed = client.post(
        "/api/auth/verify-email/confirm", json={"email": email, "code": code}
    )
    assert confirmed.status_code == 200, confirmed.text

    created = _signup(email, "9440000002")
    assert created.status_code in (200, 201), created.text
    assert created.json()["token"]


def test_the_proof_cannot_be_reused_for_a_second_account(caplog):
    """One verification, one account — otherwise a single code is a skeleton key."""
    email = "once@shop.test"
    _, code = _request_code(caplog, email)
    client.post("/api/auth/verify-email/confirm", json={"email": email, "code": code})
    assert _signup(email, "9440000003").status_code in (200, 201)

    # The address is taken now, so a second signup fails on that first — the
    # point is that the spent proof is gone, checked directly below.
    from app.database import SessionLocal
    from app.models import EmailVerification

    session = SessionLocal()
    try:
        remaining = (
            session.query(EmailVerification)
            .filter(EmailVerification.email == email)
            .count()
        )
    finally:
        session.close()
    assert remaining == 0


def test_a_wrong_code_is_rejected(caplog):
    email = "wrongcode@shop.test"
    _request_code(caplog, email)
    response = client.post(
        "/api/auth/verify-email/confirm", json={"email": email, "code": "000000"}
    )
    assert response.status_code == 400


def test_guessing_is_locked_out_after_the_attempt_limit(caplog):
    email = "bruteforce@shop.test"
    _, code = _request_code(caplog, email)
    for _ in range(auth_router.VERIFICATION_MAX_ATTEMPTS):
        client.post(
            "/api/auth/verify-email/confirm", json={"email": email, "code": "111111"}
        )

    # Even the correct code is refused once the limit is spent.
    response = client.post(
        "/api/auth/verify-email/confirm", json={"email": email, "code": code}
    )
    assert response.status_code == 429


def test_a_new_code_invalidates_the_previous_one(caplog):
    email = "replaced@shop.test"
    _, first = _request_code(caplog, email)
    _, second = _request_code(caplog, email)
    assert first != second

    stale = client.post(
        "/api/auth/verify-email/confirm", json={"email": email, "code": first}
    )
    assert stale.status_code == 400

    fresh = client.post(
        "/api/auth/verify-email/confirm", json={"email": email, "code": second}
    )
    assert fresh.status_code == 200


def test_a_registered_address_is_told_to_sign_in(caplog):
    email = "taken@shop.test"
    _, code = _request_code(caplog, email)
    client.post("/api/auth/verify-email/confirm", json={"email": email, "code": code})
    assert _signup(email, "9440000004").status_code in (200, 201)

    response, _ = _request_code(caplog, email)
    assert response.status_code == 409
    assert "Sign in" in response.json()["detail"]


def test_a_malformed_address_is_refused(caplog):
    response, _ = _request_code(caplog, "not-an-email")
    assert response.status_code == 422


def test_the_resend_cooldown_holds(caplog):
    auth_router.VERIFICATION_COOLDOWN_SECONDS = 60
    try:
        email = "cooldown@shop.test"
        first, _ = _request_code(caplog, email)
        assert first.status_code == 200
        second, _ = _request_code(caplog, email)
        assert second.status_code == 429
    finally:
        auth_router.VERIFICATION_COOLDOWN_SECONDS = 0
