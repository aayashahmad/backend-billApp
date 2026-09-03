"""
Changing your password while signed in.

This path exists so the routine case never depends on email delivery. It is
still a credential change, so it has to prove the person at the keyboard
knows the current password.
"""

import os
import tempfile

os.environ.setdefault("DATABASE_URL", "sqlite:///" + tempfile.mktemp(suffix=".db"))
os.environ.pop("SECRET_KEY", None)
for _var in ("BREVO_API_KEY", "MAIL_FROM", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
    os.environ[_var] = ""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

PHONE = "9330000001"
OLD = "startpass1"
NEW = "changedpass2"


@pytest.fixture(scope="module")
def auth():
    response = client.post(
        "/api/auth/signup",
        json={
            "username": "Password Changer",
            "email": "changer@shop.test",
            "phone": PHONE,
            "password": OLD,
        },
    )
    assert response.status_code in (200, 201), response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_requires_authentication():
    response = client.put(
        "/api/auth/password",
        json={"current_password": OLD, "new_password": NEW},
    )
    assert response.status_code in (401, 403)


def test_wrong_current_password_is_refused(auth):
    response = client.put(
        "/api/auth/password",
        headers=auth,
        json={"current_password": "not-my-password", "new_password": NEW},
    )
    assert response.status_code == 403
    # The old password still works, so nothing was changed.
    assert (
        client.post(
            "/api/auth/login", json={"login": PHONE, "password": OLD}
        ).status_code
        == 200
    )


def test_short_new_password_is_refused(auth):
    response = client.put(
        "/api/auth/password",
        headers=auth,
        json={"current_password": OLD, "new_password": "123"},
    )
    assert response.status_code == 422


def test_reusing_the_same_password_is_refused(auth):
    response = client.put(
        "/api/auth/password",
        headers=auth,
        json={"current_password": OLD, "new_password": OLD},
    )
    assert response.status_code == 422


def test_password_changes_without_touching_email(auth):
    response = client.put(
        "/api/auth/password",
        headers=auth,
        json={"current_password": OLD, "new_password": NEW},
    )
    assert response.status_code == 204, response.text

    assert (
        client.post(
            "/api/auth/login", json={"login": PHONE, "password": NEW}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/auth/login", json={"login": PHONE, "password": OLD}
        ).status_code
        == 401
    )


def test_pending_reset_codes_are_retired_by_a_password_change(auth, monkeypatch):
    """
    A code already mailed out was issued against the old password. Leaving it
    usable would let whoever holds that inbox undo the change.
    """
    from app.routers import auth as auth_router

    auth_router.RESEND_COOLDOWN_SECONDS = 0
    issued = client.post("/api/auth/forgot-password", json={"phone": PHONE})
    assert issued.status_code == 200

    changed = client.put(
        "/api/auth/password",
        headers=auth,
        json={"current_password": NEW, "new_password": "thirdpass3"},
    )
    assert changed.status_code == 204

    from app.database import SessionLocal
    from app.models import PasswordResetCode, User

    session = SessionLocal()
    try:
        user = session.query(User).filter(User.phone == PHONE).first()
        pending = (
            session.query(PasswordResetCode)
            .filter(
                PasswordResetCode.user_id == user.id,
                PasswordResetCode.used_at.is_(None),
            )
            .count()
        )
    finally:
        session.close()

    assert pending == 0
