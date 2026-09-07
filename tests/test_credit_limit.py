"""
Per-customer credit limits.

The limit is a shop's own policy, not an accounting fact: setting one must
never move a customer's balances, and NULL (no limit) must stay distinct
from 0 (no credit at all).
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
            "username": "Limit Shop",
            "email": "limits@shop.test",
            "phone": "9550000001",
            "password": "limitpass1",
        },
    )
    assert response.status_code in (200, 201), response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture(scope="module")
def customer(auth):
    created = client.post(
        "/api/bills",
        headers=auth,
        data={
            "phone": "9551110001",
            "customer_name": "Credit Customer",
            "items": '[{"item_name": "Rice", "qty": 1, "rate": 500}]',
            "payment_type": "cash",
            "amount_paid": "200",
        },
    )
    assert created.status_code in (200, 201), created.text
    return created.json()["customer"]["id"]


def test_new_customers_have_no_limit(auth, customer):
    body = client.get(f"/api/customers/{customer}", headers=auth).json()
    assert body["credit_limit"] is None
    assert body["total_unpaid"] == 300


def test_setting_a_limit_leaves_the_balances_alone(auth, customer):
    response = client.put(
        f"/api/customers/{customer}", headers=auth, json={"credit_limit": 1000}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["credit_limit"] == 1000
    # A policy setting must not rewrite what is actually owed.
    assert body["total_unpaid"] == 300
    assert body["total_amount"] == 500


def test_zero_is_a_real_limit_meaning_no_credit(auth, customer):
    body = client.put(
        f"/api/customers/{customer}", headers=auth, json={"credit_limit": 0}
    ).json()
    assert body["credit_limit"] == 0


def test_clearing_is_distinct_from_setting_zero(auth, customer):
    body = client.put(
        f"/api/customers/{customer}",
        headers=auth,
        json={"clear_credit_limit": True},
    ).json()
    assert body["credit_limit"] is None


def test_omitting_the_field_leaves_the_limit_untouched(auth, customer):
    client.put(f"/api/customers/{customer}", headers=auth, json={"credit_limit": 750})
    body = client.put(f"/api/customers/{customer}", headers=auth, json={}).json()
    assert body["credit_limit"] == 750


def test_a_negative_limit_is_refused(auth, customer):
    response = client.put(
        f"/api/customers/{customer}", headers=auth, json={"credit_limit": -50}
    )
    assert response.status_code == 422


def test_the_limit_travels_with_lists_and_search(auth, customer):
    listed = client.get("/api/customers", headers=auth).json()
    assert any(c["credit_limit"] == 750 for c in listed)
    found = client.get(
        "/api/customers/search", headers=auth, params={"q": "Credit"}
    ).json()
    assert found[0]["credit_limit"] == 750


def test_another_shop_cannot_set_your_customers_limit(auth, customer):
    other = client.post(
        "/api/auth/signup",
        json={
            "username": "Other Shop",
            "email": "other-limits@shop.test",
            "phone": "9550000002",
            "password": "otherpass1",
        },
    )
    headers = {"Authorization": f"Bearer {other.json()['token']}"}
    response = client.put(
        f"/api/customers/{customer}", headers=headers, json={"credit_limit": 1}
    )
    assert response.status_code == 404
