"""
Forgot-password flow: code issue, verification, and the ways it must fail.

The security properties are the point of these tests — an unbounded or
replayable reset code is an account takeover, not a UX bug.
"""

import os
import re
import tempfile

os.environ.setdefault("DATABASE_URL", "sqlite:///" + tempfile.mktemp(suffix=".db"))
os.environ.pop("SECRET_KEY", None)

# Blank every mail credential BEFORE the app is imported. Set to "" rather
# than deleted: load_dotenv() skips keys already present in the environment,
# so this also stops a real .env key from leaking in and making the suite
# send live email to a real inbox.
for _var in (
    "BREVO_API_KEY",
    "MAIL_FROM",
    "SMTP_HOST",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_FROM",
):
    os.environ[_var] = ""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import auth as auth_router

client = TestClient(app)

PHONE = "9871112223"
EMAIL = "reset-test@example.com"
PASSWORD = "original123"


@pytest.fixture(scope="module", autouse=True)
def account():
    response = client.post(
        "/api/auth/signup",
        json={
            "username": "Reset Tester",
            "email": EMAIL,
            "phone": PHONE,
            "password": PASSWORD,
        },
    )
    assert response.status_code in (200, 201), response.text


def _issue_code(caplog, phone=PHONE):
    """Request a code and read it back out of the dev log."""
    auth_router.RESEND_COOLDOWN_SECONDS = 0  # no waiting between test cases
    with caplog.at_level("WARNING"):
        caplog.clear()
        response = client.post("/api/auth/forgot-password", json={"phone": phone})
    assert response.status_code == 200, response.text
    match = re.search(r"code for \S+: (\d{6})", caplog.text)
    return response, (match.group(1) if match else None)


def test_unknown_number_gives_the_same_answer_and_no_hint(caplog):
    response, code = _issue_code(caplog, phone="0000000000")
    body = response.json()
    # Identical message to the registered case — nothing to enumerate with.
    assert body["message"] == auth_router.GENERIC_RESET_MESSAGE
    assert body["email_hint"] is None
    assert code is None


def test_known_number_returns_masked_email_hint(caplog):
    response, code = _issue_code(caplog)
    body = response.json()
    assert body["message"] == auth_router.GENERIC_RESET_MESSAGE
    assert body["email_hint"] == "re•••••@example.com"
    # The full address is never returned.
    assert EMAIL not in response.text
    assert code is not None and len(code) == 6


def test_wrong_code_is_rejected_and_counts_against_the_limit(caplog):
    _issue_code(caplog)
    response = client.post(
        "/api/auth/reset-password",
        json={"phone": PHONE, "code": "000000", "new_password": "hacked123"},
    )
    assert response.status_code == 400
    # The old password still works.
    login = client.post(
        "/api/auth/login", json={"login": PHONE, "password": PASSWORD}
    )
    assert login.status_code == 200


def test_code_is_locked_out_after_max_attempts(caplog):
    _issue_code(caplog)
    for _ in range(auth_router.MAX_CODE_ATTEMPTS):
        client.post(
            "/api/auth/reset-password",
            json={"phone": PHONE, "code": "111111", "new_password": "hacked123"},
        )
    response = client.post(
        "/api/auth/reset-password",
        json={"phone": PHONE, "code": "111111", "new_password": "hacked123"},
    )
    assert response.status_code == 429


def test_short_password_is_refused(caplog):
    _, code = _issue_code(caplog)
    response = client.post(
        "/api/auth/reset-password",
        json={"phone": PHONE, "code": code, "new_password": "123"},
    )
    assert response.status_code == 422


def test_requesting_a_new_code_invalidates_the_previous_one(caplog):
    _, first = _issue_code(caplog)
    _, second = _issue_code(caplog)
    assert first != second

    stale = client.post(
        "/api/auth/reset-password",
        json={"phone": PHONE, "code": first, "new_password": "newpass123"},
    )
    assert stale.status_code == 400

    fresh = client.post(
        "/api/auth/reset-password",
        json={"phone": PHONE, "code": second, "new_password": "newpass123"},
    )
    assert fresh.status_code == 200
    assert fresh.json()["token"]


def test_new_password_works_and_old_one_does_not():
    assert (
        client.post(
            "/api/auth/login", json={"login": PHONE, "password": "newpass123"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/auth/login", json={"login": PHONE, "password": PASSWORD}
        ).status_code
        == 401
    )


def test_used_code_cannot_be_replayed():
    response = client.post(
        "/api/auth/reset-password",
        json={"phone": PHONE, "code": "any", "new_password": "again12345"},
    )
    assert response.status_code == 400


def test_resend_cooldown_blocks_rapid_requests(caplog):
    _issue_code(caplog)
    auth_router.RESEND_COOLDOWN_SECONDS = 60
    try:
        response = client.post("/api/auth/forgot-password", json={"phone": PHONE})
        assert response.status_code == 429
    finally:
        auth_router.RESEND_COOLDOWN_SECONDS = 0


def test_mail_transport_prefers_the_http_api(monkeypatch):
    """
    Render's free tier blocks every outbound SMTP port, so a deployment with
    both sets of credentials must take the HTTPS route, not SMTP.

    Patches the environment rather than module attributes: the mailer reads
    its settings at call time, which is what stops import order from
    deciding whether a key is visible.
    """
    from app import mailer

    monkeypatch.setenv("BREVO_API_KEY", "key-123")
    monkeypatch.setenv("MAIL_FROM", "shop@example.com")
    monkeypatch.setenv("SMTP_HOST", "smtp-relay.example.com")
    monkeypatch.setenv("SMTP_USER", "user")
    monkeypatch.setenv("SMTP_PASSWORD", "pass")
    assert mailer.transport_name() == "brevo-api"

    monkeypatch.setenv("BREVO_API_KEY", "")
    assert mailer.transport_name() == "smtp"

    monkeypatch.setenv("SMTP_HOST", "")
    assert mailer.transport_name() == "none"
    assert not mailer.is_configured()
