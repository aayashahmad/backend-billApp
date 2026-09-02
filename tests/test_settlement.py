"""
Settlement maths end-to-end: bills, dues, payments, and overpayment.

Runs against a throwaway sqlite database with the real routers — the same
paths the app exercises. These figures mirror the manual QA scenario:
    bill 835 paid 500  -> owes 335
    payment 200        -> owes 135
    bill 100 paid 235  -> owes 0   (the excess settles the old dues)
"""

import os
import tempfile

os.environ.setdefault("DATABASE_URL", "sqlite:///" + tempfile.mktemp(suffix=".db"))
os.environ.pop("SECRET_KEY", None)  # exercise the sqlite dev fallback

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _signup_and_token():
    response = client.post(
        "/api/auth/signup",
        json={
            "username": "QA Tester",
            "email": "qa@settlement.test",
            "phone": "9990001111",
            "password": "qa12345",
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["token"]


@pytest.fixture(scope="module")
def auth():
    token = _signup_and_token()
    return {"Authorization": f"Bearer {token}"}


def test_signup_rejects_short_password():
    response = client.post(
        "/api/auth/signup",
        json={
            "username": "x",
            "email": "short@pw.test",
            "phone": "1112223333",
            "password": "123",
        },
    )
    assert response.status_code == 422


def test_multi_item_bill_records_both_lines(auth):
    response = client.post(
        "/api/bills",
        headers=auth,
        data={
            "phone": "7000111222",
            "customer_name": "Ravi Kumar",
            "items": (
                '[{"item_name": "Rice 5kg", "qty": 2, "rate": 350},'
                ' {"item_name": "Sugar 1kg", "qty": 3, "rate": 45}]'
            ),
            "payment_type": "cash",
            "amount_paid": "500",
        },
    )
    assert response.status_code in (200, 201), response.text
    body = response.json()
    bill = body["bill"]
    assert bill["bill_total"] == 835.0
    assert [i["line_total"] for i in bill["items"]] == [700.0, 135.0]
    # Flat mirror fields keep old clients readable.
    assert bill["item_name"] == "Rice 5kg"
    assert body["customer"]["total_unpaid"] == 335.0


def test_payment_reduces_outstanding(auth):
    customers = client.get("/api/customers", headers=auth).json()
    customer_id = customers[0]["id"]

    response = client.post(
        f"/api/customers/{customer_id}/payments",
        headers=auth,
        data={"amount": "200", "payment_type": "cash"},
    )
    assert response.status_code in (200, 201), response.text

    detail = client.get(f"/api/customers/{customer_id}", headers=auth).json()
    assert detail["total_unpaid"] == 135.0


def test_payment_cannot_exceed_outstanding(auth):
    customers = client.get("/api/customers", headers=auth).json()
    customer_id = customers[0]["id"]

    response = client.post(
        f"/api/customers/{customer_id}/payments",
        headers=auth,
        data={"amount": "500", "payment_type": "cash"},
    )
    assert response.status_code == 422


def test_overpaid_bill_settles_old_dues(auth):
    response = client.post(
        "/api/bills",
        headers=auth,
        data={
            "phone": "7000111222",
            "customer_name": "Ravi Kumar",
            "items": '[{"item_name": "Milk", "qty": 1, "rate": 100}]',
            "payment_type": "cash",
            "amount_paid": "235",
        },
    )
    assert response.status_code in (200, 201), response.text
    body = response.json()
    assert body["bill"]["unbalance"] == 0.0
    assert body["customer"]["total_unpaid"] == 0.0


def test_bill_paid_beyond_all_dues_is_rejected(auth):
    response = client.post(
        "/api/bills",
        headers=auth,
        data={
            "phone": "7000111222",
            "customer_name": "Ravi Kumar",
            "items": '[{"item_name": "Bread", "qty": 1, "rate": 50}]',
            "payment_type": "cash",
            "amount_paid": "51",
        },
    )
    assert response.status_code == 422


def test_detail_serialises_items_without_other_users_data(auth):
    customers = client.get("/api/customers", headers=auth).json()
    detail = client.get(f"/api/customers/{customers[0]['id']}", headers=auth).json()
    bills = {bill["id"]: bill for bill in detail["bills"]}
    assert any(len(bill["items"]) == 2 for bill in bills.values())
    assert detail["payments"][0]["amount"] == 200.0
