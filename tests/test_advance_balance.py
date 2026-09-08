"""
Advance balances — money paid ahead of a bill.

The arithmetic here decides what a shop believes it is owed, so these tests
pin down where every rupee goes: what clears a debt, what is held as credit,
and what the next bill draws down.
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


@pytest.fixture(scope="module")
def auth():
    response = client.post(
        "/api/auth/signup",
        json={
            "username": "Advance Shop",
            "email": "advance@shop.test",
            "phone": "9770000001",
            "password": "advancepass1",
        },
    )
    assert response.status_code in (200, 201), response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _bill(auth, phone, total, paid, name="Advance Customer"):
    response = client.post(
        "/api/bills",
        headers=auth,
        data={
            "phone": phone,
            "customer_name": name,
            "items": f'[{{"item_name": "Item", "qty": 1, "rate": {total}}}]',
            "payment_type": "cash",
            "amount_paid": str(paid),
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def _pay(auth, customer_id, amount):
    response = client.post(
        f"/api/customers/{customer_id}/payments",
        headers=auth,
        data={"amount": str(amount), "payment_type": "cash"},
    )
    return response


def test_overpaying_a_bill_banks_the_surplus(auth):
    """500 bill, 800 handed over, nothing owed before: 300 is paid ahead."""
    body = _bill(auth, "9771110001", 500, 800)
    customer = body["customer"]
    assert customer["total_unpaid"] == 0
    assert customer["advance_balance"] == 300


def test_the_next_bill_draws_the_advance_down_first(auth):
    """A 200 bill against a 300 advance leaves 100 credit and no debt."""
    body = _bill(auth, "9771110001", 200, 0)
    customer = body["customer"]
    assert customer["total_unpaid"] == 0
    assert customer["advance_balance"] == 100


def test_an_advance_smaller_than_the_bill_covers_what_it_can(auth):
    """100 credit against a 400 bill with nothing paid leaves 300 owed."""
    body = _bill(auth, "9771110001", 400, 0)
    customer = body["customer"]
    assert customer["advance_balance"] == 0
    assert customer["total_unpaid"] == 300


def test_a_payment_beyond_the_debt_settles_then_banks(auth):
    """300 owed, 500 paid: the debt clears and 200 is held."""
    detail = client.get("/api/customers", headers=auth).json()
    customer_id = next(c["id"] for c in detail if c["phone"] == "9771110001")

    response = _pay(auth, customer_id, 500)
    assert response.status_code in (200, 201), response.text

    after = client.get(f"/api/customers/{customer_id}", headers=auth).json()
    assert after["total_unpaid"] == 0
    assert after["advance_balance"] == 200


def test_paying_a_customer_who_owes_nothing_is_all_advance(auth):
    """Paying ahead before any bill exists is the point of the feature."""
    created = _bill(auth, "9771110002", 100, 100, name="Prepaid Customer")
    customer_id = created["customer"]["id"]
    assert created["customer"]["total_unpaid"] == 0

    response = _pay(auth, customer_id, 1000)
    assert response.status_code in (200, 201), response.text

    after = client.get(f"/api/customers/{customer_id}", headers=auth).json()
    assert after["advance_balance"] == 1000
    assert after["total_unpaid"] == 0


def test_a_zero_payment_is_still_refused(auth):
    detail = client.get("/api/customers", headers=auth).json()
    customer_id = next(c["id"] for c in detail if c["phone"] == "9771110002")
    assert _pay(auth, customer_id, 0).status_code == 422


def test_balances_never_go_negative(auth):
    """
    Whatever the combination, neither figure may drop below zero: every
    screen reads them as money, and a negative would render as nonsense.
    """
    listed = client.get("/api/customers", headers=auth).json()
    for customer in listed:
        assert customer["total_unpaid"] >= 0
        assert customer["advance_balance"] >= 0


def test_advance_and_debt_are_never_held_at_once(auth):
    """
    Owing money while holding credit would be two contradictory statements
    about the same relationship.
    """
    listed = client.get("/api/customers", headers=auth).json()
    for customer in listed:
        assert not (customer["total_unpaid"] > 0 and customer["advance_balance"] > 0), (
            f"{customer['name']} owes {customer['total_unpaid']} "
            f"while holding {customer['advance_balance']}"
        )


def test_a_payment_records_what_the_money_did(auth):
    """
    A receipt printed later must show what happened when the money changed
    hands, so the split is stored on the payment rather than derived from
    balances that will have moved on.
    """
    created = _bill(auth, "9771110009", 1000, 0, name="Receipt Customer")
    customer_id = created["customer"]["id"]

    _pay(auth, customer_id, 1500)

    detail = client.get(f"/api/customers/{customer_id}", headers=auth).json()
    payment = detail["payments"][0]

    assert payment["amount"] == 1500
    assert payment["applied_to_dues"] == 1000
    assert payment["advance_added"] == 500
    assert payment["outstanding_after"] == 0
    assert payment["advance_balance_after"] == 500
    # Every rupee is accounted for.
    assert payment["applied_to_dues"] + payment["advance_added"] == payment["amount"]


def test_a_pure_advance_records_no_settlement(auth):
    created = _bill(auth, "9771110010", 200, 200, name="Prepay Only")
    customer_id = created["customer"]["id"]

    _pay(auth, customer_id, 700)

    detail = client.get(f"/api/customers/{customer_id}", headers=auth).json()
    payment = detail["payments"][0]
    assert payment["applied_to_dues"] == 0
    assert payment["advance_added"] == 700
    assert payment["advance_balance_after"] == 700
