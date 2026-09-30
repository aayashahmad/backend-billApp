"""
Payment reminders: who gets chased, and who must not be.

These rules decide what lands in a customer's inbox without anybody pressing
send, so the failures that matter are the false positives — chasing somebody
who paid, or who never agreed to be chased. Each of those has its own test.
"""

import os
import tempfile
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:///" + tempfile.mktemp(suffix=".db"))
os.environ.pop("SECRET_KEY", None)
os.environ["REMINDER_SECRET"] = "test-secret"
for _var in ("BREVO_API_KEY", "MAIL_FROM", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
    os.environ[_var] = ""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import mark_email_verified

client = TestClient(app)

_phones = iter(range(1000, 9999))


@pytest.fixture(scope="module")
def auth():
    mark_email_verified("reminders@shop.test")
    response = client.post(
        "/api/auth/signup",
        json={
            "username": "Reminder Shop",
            "email": "reminders@shop.test",
            "phone": "9770000101",
            "password": "remindpass1",
        },
    )
    assert response.status_code in (200, 201), response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _new_phone():
    return f"97711{next(_phones):05d}"


def _owing_customer(auth, amount, *, name="Overdue Customer", age_days=0):
    """A customer owing money, with the debt backdated where asked."""
    phone = _new_phone()
    response = client.post(
        "/api/bills",
        headers=auth,
        data={
            "phone": phone,
            "customer_name": name,
            "items": f'[{{"item_name": "Item", "qty": 1, "rate": {amount}}}]',
            "payment_type": "credit",
        },
    )
    assert response.status_code in (200, 201), response.text
    customer_id = response.json()["customer"]["id"]

    if age_days:
        # Reach past the API: there is no way to create an old bill through
        # it, and "overdue" is meaningless without one.
        from app.database import SessionLocal
        from app.models import Bill

        session = SessionLocal()
        try:
            when = datetime.utcnow() - timedelta(days=age_days)
            for bill in session.query(Bill).filter(Bill.customer_id == customer_id):
                bill.created_at = when
            session.commit()
        finally:
            session.close()

    return customer_id


def _due_ids(auth):
    response = client.get("/api/reminders/due", headers=auth)
    assert response.status_code == 200, response.text
    return {row["id"] for row in response.json()}


def test_a_fresh_debt_is_not_chased(auth):
    """Today's unpaid bill is not overdue; it is just today's business."""
    customer_id = _owing_customer(auth, 5000)
    assert customer_id not in _due_ids(auth)


def test_an_old_debt_over_the_threshold_is_listed(auth):
    customer_id = _owing_customer(auth, 5000, age_days=30)
    assert customer_id in _due_ids(auth)


def test_a_small_balance_is_left_alone(auth):
    """Below the shop's floor, however old. Chasing ₹20 costs more than ₹20."""
    customer_id = _owing_customer(auth, 20, age_days=90)
    assert customer_id not in _due_ids(auth)


def test_a_customer_who_paid_is_never_chased(auth):
    customer_id = _owing_customer(auth, 5000, age_days=30)
    assert customer_id in _due_ids(auth)

    settle = client.post(
        f"/api/customers/{customer_id}/payments",
        headers=auth,
        data={"amount": "5000", "payment_type": "cash"},
    )
    assert settle.status_code in (200, 201), settle.text
    assert customer_id not in _due_ids(auth)


def test_a_customer_can_be_switched_off(auth):
    """Some customers are family. The shop must be able to say so."""
    customer_id = _owing_customer(auth, 5000, age_days=30)
    assert customer_id in _due_ids(auth)

    response = client.put(
        f"/api/reminders/customers/{customer_id}",
        headers=auth,
        json={"reminder_enabled": False},
    )
    assert response.status_code == 200, response.text
    assert customer_id not in _due_ids(auth)


def test_a_customer_threshold_overrides_the_shop(auth):
    customer_id = _owing_customer(auth, 500, age_days=30)
    assert customer_id in _due_ids(auth)

    # This one is only worth chasing above ₹2,000.
    client.put(
        f"/api/reminders/customers/{customer_id}",
        headers=auth,
        json={"reminder_min_amount": 2000},
    )
    assert customer_id not in _due_ids(auth)


def test_zero_is_a_real_threshold_not_an_absent_one(auth):
    """A floor of 0 means chase any balance, and must not read as unset."""
    customer_id = _owing_customer(auth, 5, age_days=30)
    assert customer_id not in _due_ids(auth)

    client.put(
        f"/api/reminders/customers/{customer_id}",
        headers=auth,
        json={"reminder_min_amount": 0},
    )
    assert customer_id in _due_ids(auth)


def test_the_daily_run_refuses_without_the_secret(auth):
    assert client.post("/api/reminders/run").status_code == 403
    assert (
        client.post(
            "/api/reminders/run", headers={"X-Reminder-Secret": "wrong"}
        ).status_code
        == 403
    )


def test_the_daily_run_skips_a_shop_that_never_opted_in(auth):
    """The master switch is off by default, and off means nothing goes out."""
    _owing_customer(auth, 5000, age_days=30)

    response = client.post(
        "/api/reminders/run", headers={"X-Reminder-Secret": "test-secret"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["emails_sent"] == 0
    assert response.json()["shops_processed"] == 0


def test_settings_round_trip(auth):
    response = client.put(
        "/api/reminders/settings",
        headers=auth,
        json={"reminders_enabled": True, "reminder_min_amount": 250,
              "reminder_after_days": 14},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reminders_enabled"] is True
    assert body["reminder_min_amount"] == 250
    assert body["reminder_after_days"] == 14

    # And the new floor takes effect immediately.
    customer_id = _owing_customer(auth, 200, age_days=30)
    assert customer_id not in _due_ids(auth)

    client.put(
        "/api/reminders/settings",
        headers=auth,
        json={"reminders_enabled": False, "reminder_min_amount": 100,
              "reminder_after_days": 7},
    )


def test_an_absurd_overdue_window_is_refused(auth):
    """Ten years would silently mean 'never remind anybody' — refuse it."""
    response = client.put(
        "/api/reminders/settings", headers=auth, json={"reminder_after_days": 3650}
    )
    assert response.status_code == 422


def test_another_shop_cannot_change_your_customer(auth):
    customer_id = _owing_customer(auth, 5000, age_days=30)

    mark_email_verified("rival-reminders@shop.test")
    other = client.post(
        "/api/auth/signup",
        json={
            "username": "Rival Shop",
            "email": "rival-reminders@shop.test",
            "phone": "9770000102",
            "password": "rivalpass1",
        },
    )
    headers = {"Authorization": f"Bearer {other.json()['token']}"}

    response = client.put(
        f"/api/reminders/customers/{customer_id}",
        headers=headers,
        json={"reminder_enabled": False},
    )
    assert response.status_code == 404
