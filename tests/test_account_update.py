"""
Editing your own account details.

Email and phone are credentials here — email receives reset codes, phone is a
login identifier — so these tests care as much about who is allowed to change
them as about the happy path.
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

PASSWORD = "ownerpass1"


def _signup(username, email, phone):
    response = client.post(
        "/api/auth/signup",
        json={
            "username": username,
            "email": email,
            "phone": phone,
            "password": PASSWORD,
        },
    )
    assert response.status_code in (200, 201), response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture(scope="module")
def owner():
    return _signup("Owner One", "owner1@shop.test", "9000000001")


@pytest.fixture(scope="module")
def other(owner):
    """A second account, so uniqueness collisions can be provoked."""
    return _signup("Owner Two", "owner2@shop.test", "9000000002")


def _put(auth, **overrides):
    body = {
        "username": "Owner One",
        "email": "owner1@shop.test",
        "phone": "9000000001",
    }
    body.update(overrides)
    return client.put("/api/auth/me", headers=auth, json=body)


def test_requires_authentication():
    assert client.put("/api/auth/me", json={}).status_code in (401, 403)


def test_name_alone_changes_without_a_password(owner):
    response = _put(owner, username="Renamed Owner")
    assert response.status_code == 200, response.text
    assert response.json()["username"] == "Renamed Owner"


def test_blank_name_is_rejected(owner):
    assert _put(owner, username="   ").status_code == 422


def test_invalid_email_is_rejected(owner):
    assert _put(owner, email="not-an-email").status_code == 422


def test_non_numeric_phone_is_rejected(owner):
    assert _put(owner, phone="98abc76543").status_code == 422


def test_changing_email_without_password_is_refused(owner):
    response = _put(owner, username="Renamed Owner", email="new1@shop.test")
    assert response.status_code == 403
    assert "current password" in response.json()["detail"].lower()


def test_changing_email_with_wrong_password_is_refused(owner):
    response = _put(
        owner,
        username="Renamed Owner",
        email="new1@shop.test",
        current_password="wrongpass",
    )
    assert response.status_code == 403


def test_email_already_taken_is_reported(owner, other):
    response = _put(
        owner,
        username="Renamed Owner",
        email="owner2@shop.test",
        current_password=PASSWORD,
    )
    assert response.status_code == 409
    assert "already used" in response.json()["detail"]


def test_phone_already_taken_is_reported(owner, other):
    response = _put(
        owner,
        username="Renamed Owner",
        phone="9000000002",
        current_password=PASSWORD,
    )
    assert response.status_code == 409


def test_email_and_phone_change_with_correct_password(owner):
    response = _put(
        owner,
        username="Renamed Owner",
        email="Owner1.New@Shop.Test",
        phone="9000000009",
        current_password=PASSWORD,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # Stored lowercase, matching how signup and login normalise it.
    assert body["email"] == "owner1.new@shop.test"
    assert body["phone"] == "9000000009"


def test_can_log_in_with_the_new_identifiers(owner):
    for identifier in ("owner1.new@shop.test", "9000000009"):
        response = client.post(
            "/api/auth/login", json={"login": identifier, "password": PASSWORD}
        )
        assert response.status_code == 200, f"{identifier}: {response.text}"

    stale = client.post(
        "/api/auth/login", json={"login": "9000000001", "password": PASSWORD}
    )
    assert stale.status_code == 401


def test_pending_reset_codes_are_retired_when_contact_changes(owner):
    """A code mailed to the old address must not survive the change."""
    issued = client.post(
        "/api/auth/forgot-password", json={"phone": "9000000009"}
    )
    assert issued.status_code == 200

    changed = _put(
        owner,
        username="Renamed Owner",
        email="owner1.final@shop.test",
        phone="9000000009",
        current_password=PASSWORD,
    )
    assert changed.status_code == 200

    # The outstanding code is now spent, whatever its digits were.
    from app.database import SessionLocal
    from app.models import PasswordResetCode, User

    session = SessionLocal()
    try:
        user = session.query(User).filter(User.phone == "9000000009").first()
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


def test_customer_search_matches_names_and_escapes_wildcards(owner):
    """The bill form's picker searches on partial names, not just phones."""
    for name, phone in (
        ("Ramesh Sharma", "9111000001"),
        ("Ramya Iyer", "9111000002"),
        ("100% Cotton Traders", "9111000003"),
    ):
        created = client.post(
            "/api/bills",
            headers=owner,
            data={
                "phone": phone,
                "customer_name": name,
                "items": '[{"item_name": "Item", "qty": 1, "rate": 10}]',
                "payment_type": "cash",
                "amount_paid": "10",
            },
        )
        assert created.status_code in (200, 201), created.text

    hits = client.get("/api/customers/search", headers=owner, params={"q": "ram"}).json()
    names = [c["name"] for c in hits]
    assert "Ramesh Sharma" in names and "Ramya Iyer" in names
    # Results carry the dues, so the picker can show them without a second call.
    assert all("total_unpaid" in c for c in hits)

    # A literal % must not behave as a wildcard matching every customer.
    wild = client.get("/api/customers/search", headers=owner, params={"q": "%"}).json()
    assert [c["name"] for c in wild] == ["100% Cotton Traders"]

    capped = client.get(
        "/api/customers/search", headers=owner, params={"q": "ram", "limit": 1}
    ).json()
    assert len(capped) == 1


def test_reset_code_follows_the_account_its_phone_belongs_to(monkeypatch):
    """
    Every owner has their own email, and the code must go to the address on
    the account being recovered — not to the sender identity, and not to some
    other owner. After an email change it must follow the NEW address.
    """
    from app import mailer
    from app.routers import auth as auth_router

    sent = []
    monkeypatch.setattr(mailer, "is_configured", lambda: True)
    monkeypatch.setattr(auth_router, "is_configured", lambda: True)
    monkeypatch.setattr(
        auth_router,
        "send_password_reset_code",
        lambda to, code, minutes: sent.append((to, code)),
    )
    auth_router.RESEND_COOLDOWN_SECONDS = 0

    alice = _signup("Alice", "alice.first@shop.test", "9220000001")
    _signup("Bob", "bob@shop.test", "9220000002")

    # Each owner's code goes to their own address.
    client.post("/api/auth/forgot-password", json={"phone": "9220000001"})
    client.post("/api/auth/forgot-password", json={"phone": "9220000002"})
    assert sent[0][0] == "alice.first@shop.test"
    assert sent[1][0] == "bob@shop.test"
    assert sent[0][1] != sent[1][1], "codes must not be shared between accounts"

    # Alice changes her email; the next code must follow it.
    changed = client.put(
        "/api/auth/me",
        headers=alice,
        json={
            "username": "Alice",
            "email": "alice.second@shop.test",
            "phone": "9220000001",
            "current_password": PASSWORD,
        },
    )
    assert changed.status_code == 200, changed.text

    sent.clear()
    response = client.post("/api/auth/forgot-password", json={"phone": "9220000001"})
    assert response.status_code == 200
    assert sent[0][0] == "alice.second@shop.test", sent
    # Bob is untouched by Alice's change.
    assert response.json()["email_hint"] == "al•••••@shop.test"
