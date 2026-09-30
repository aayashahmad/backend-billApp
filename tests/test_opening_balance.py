"""
Balances carried over from a shop's paper khata.

Two things can go wrong here and both are expensive: the figure lands on
the wrong side of the account, or it leaks into the shop's sales. Neither
announces itself — the shop finds out months later, from a customer.
"""

import os
import tempfile
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:///" + tempfile.mktemp(suffix=".db"))
os.environ.pop("SECRET_KEY", None)
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
    mark_email_verified("opening@shop.test")
    response = client.post(
        "/api/auth/signup",
        json={
            "username": "Opening Shop",
            "email": "opening@shop.test",
            "phone": "9990000201",
            "password": "openingpass1",
        },
    )
    assert response.status_code in (200, 201), response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _new_phone():
    return f"99911{next(_phones):05d}"


def _customer(auth, name="Khata Customer", bill=0, paid=0):
    """A customer, optionally with a bill so the account is not empty."""
    phone = _new_phone()
    response = client.post(
        "/api/bills",
        headers=auth,
        data={
            "phone": phone,
            "customer_name": name,
            "items": f'[{{"item_name": "Item", "qty": 1, "rate": {bill or 100}}}]',
            "payment_type": "cash",
            "amount_paid": str(paid if bill else 100),
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["customer"]["id"]


def _set(auth, customer_id, **payload):
    response = client.put(
        f"/api/customers/{customer_id}/opening-balance", headers=auth, json=payload
    )
    assert response.status_code == 200, response.text
    return response.json()


def _customer_state(auth, customer_id):
    response = client.get(f"/api/customers/{customer_id}", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


# ── Which side of the account it lands on ────────────────────────────

def test_a_carried_over_debt_is_money_owed(auth):
    customer_id = _customer(auth)
    body = _set(auth, customer_id, amount=5000, balance_type="due")

    assert body["total_unpaid"] == 5000
    assert body["advance_balance"] == 0


def test_a_carried_over_credit_is_money_held(auth):
    """Not every migrated customer owes — some had paid ahead."""
    customer_id = _customer(auth)
    body = _set(auth, customer_id, amount=750, balance_type="advance")

    assert body["advance_balance"] == 750
    assert body["total_unpaid"] == 0


def test_it_adds_to_what_the_customer_already_owes(auth):
    customer_id = _customer(auth, bill=1000, paid=400)  # 600 owing
    body = _set(auth, customer_id, amount=5000, balance_type="due")

    assert body["total_unpaid"] == 5600


def test_a_carried_over_credit_settles_an_existing_debt(auth):
    """Credit and debt never coexist; the net decides which one remains."""
    customer_id = _customer(auth, bill=1000, paid=400)  # 600 owing
    body = _set(auth, customer_id, amount=1000, balance_type="advance")

    assert body["advance_balance"] == 400
    assert body["total_unpaid"] == 0


# ── Correcting a migration-day mistake ───────────────────────────────

def test_changing_it_replaces_rather_than_adds(auth):
    """
    The figure typed on migration day is often wrong, and applying a
    correction on top of the old one would double it — silently, on the day
    a shop is least able to notice.
    """
    customer_id = _customer(auth)
    _set(auth, customer_id, amount=5000, balance_type="due")
    body = _set(auth, customer_id, amount=3000, balance_type="due")

    assert body["total_unpaid"] == 3000


def test_it_can_be_corrected_across_sides(auth):
    customer_id = _customer(auth)
    _set(auth, customer_id, amount=5000, balance_type="due")
    body = _set(auth, customer_id, amount=5000, balance_type="advance")

    assert body["total_unpaid"] == 0
    assert body["advance_balance"] == 5000


def test_clearing_it_takes_the_money_back_off(auth):
    customer_id = _customer(auth, bill=1000, paid=400)  # 600 owing
    _set(auth, customer_id, amount=5000, balance_type="due")
    body = _set(auth, customer_id, amount=None)

    # Back to what the bills alone say, not zero.
    assert body["total_unpaid"] == 600
    assert body["amount"] is None


def test_it_survives_later_trading(auth):
    """A bill after migration adds to the carried-over debt, not past it."""
    customer_id = _customer(auth)
    _set(auth, customer_id, amount=5000, balance_type="due")

    phone = _customer_state(auth, customer_id)["phone"]
    client.post(
        "/api/bills",
        headers=auth,
        data={
            "phone": phone,
            "customer_name": "Khata Customer",
            "items": '[{"item_name": "Rice", "qty": 1, "rate": 500}]',
            "payment_type": "credit",
        },
    )
    assert _customer_state(auth, customer_id)["total_unpaid"] == 5500


def test_a_payment_settles_carried_over_debt(auth):
    customer_id = _customer(auth)
    _set(auth, customer_id, amount=5000, balance_type="due")

    client.post(
        f"/api/customers/{customer_id}/payments",
        headers=auth,
        data={"amount": "2000", "payment_type": "cash"},
    )
    assert _customer_state(auth, customer_id)["total_unpaid"] == 3000


# ── It is not a sale ─────────────────────────────────────────────────

def test_it_never_counts_as_takings(auth):
    """
    The whole reason this is not stored as a bill. A shop migrating a book
    of old dues must not see them as money it earned today.
    """
    before = client.get(
        "/api/reports/summary", headers=auth, params={"period": "daily"}
    ).json()

    customer_id = _customer(auth)
    _set(auth, customer_id, amount=50000, balance_type="due")

    after = client.get(
        "/api/reports/summary", headers=auth, params={"period": "daily"}
    ).json()

    assert after["totals"]["billed"] == before["totals"]["billed"] + 100, (
        "only the seeding bill should have moved the takings"
    )
    assert after["totals"]["collected"] == before["totals"]["collected"] + 100


def test_it_creates_no_bill(auth):
    customer_id = _customer(auth)
    bills_before = len(_customer_state(auth, customer_id)["bills"])
    _set(auth, customer_id, amount=5000, balance_type="due")

    assert len(_customer_state(auth, customer_id)["bills"]) == bills_before


# ── What it is for ───────────────────────────────────────────────────

def test_the_book_reference_is_kept(auth):
    """The argument at the counter a year from now."""
    customer_id = _customer(auth)
    body = _set(
        auth,
        customer_id,
        amount=5000,
        balance_type="due",
        reference="Khata 3, page 47",
        note="Includes Diwali advance",
    )
    assert body["reference"] == "Khata 3, page 47"
    assert body["note"] == "Includes Diwali advance"


def test_a_migrated_debt_is_chased_like_any_other(auth):
    """
    Without its own date a carried-over debt has no bills to age it, so the
    oldest debts in the shop would be the only ones never chased.
    """
    customer_id = _customer(auth)
    long_ago = (datetime.utcnow() - timedelta(days=200)).isoformat()
    _set(auth, customer_id, amount=5000, balance_type="due", as_of=long_ago)

    due = client.get("/api/reminders/due", headers=auth).json()
    row = next((r for r in due if r["id"] == customer_id), None)
    assert row is not None, "a 200-day-old carried-over debt must be chased"
    assert row["days_overdue"] >= 199


def test_a_carried_over_credit_is_never_chased(auth):
    customer_id = _customer(auth)
    long_ago = (datetime.utcnow() - timedelta(days=200)).isoformat()
    _set(auth, customer_id, amount=5000, balance_type="advance", as_of=long_ago)

    due = client.get("/api/reminders/due", headers=auth).json()
    assert all(row["id"] != customer_id for row in due)


# ── Bulk migration ───────────────────────────────────────────────────

def test_a_whole_book_goes_over_at_once(auth):
    ids = [_customer(auth, name=f"Bulk {i}") for i in range(3)]
    response = client.post(
        "/api/customers/opening-balances",
        headers=auth,
        json={
            "entries": [
                {"customer_id": ids[0], "amount": 1000, "balance_type": "due"},
                {"customer_id": ids[1], "amount": 250, "balance_type": "advance"},
                {"customer_id": ids[2], "amount": 0},
            ]
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["applied"] == 3

    assert _customer_state(auth, ids[0])["total_unpaid"] == 1000
    assert _customer_state(auth, ids[1])["advance_balance"] == 250
    assert _customer_state(auth, ids[2])["total_unpaid"] == 0


def test_a_bulk_migration_is_all_or_nothing(auth):
    """
    Half a migrated book is worse than none: the shop cannot tell which
    half, and every balance it trusts afterwards might be wrong.
    """
    customer_id = _customer(auth)
    response = client.post(
        "/api/customers/opening-balances",
        headers=auth,
        json={
            "entries": [
                {"customer_id": customer_id, "amount": 4000, "balance_type": "due"},
                {"customer_id": 999999, "amount": 1000, "balance_type": "due"},
            ]
        },
    )
    assert response.status_code == 404
    # The valid entry must not have been applied.
    assert _customer_state(auth, customer_id)["total_unpaid"] == 0


# ── Boundaries ───────────────────────────────────────────────────────

def test_a_negative_amount_is_refused(auth):
    """Direction is the balance_type's job; a minus sign here is a mistake."""
    customer_id = _customer(auth)
    response = client.put(
        f"/api/customers/{customer_id}/opening-balance",
        headers=auth,
        json={"amount": -500, "balance_type": "due"},
    )
    assert response.status_code == 422


def test_an_unknown_side_is_refused(auth):
    customer_id = _customer(auth)
    response = client.put(
        f"/api/customers/{customer_id}/opening-balance",
        headers=auth,
        json={"amount": 500, "balance_type": "sideways"},
    )
    assert response.status_code == 422


def test_another_shop_cannot_touch_your_customer(auth):
    customer_id = _customer(auth)

    mark_email_verified("rival-opening@shop.test")
    other = client.post(
        "/api/auth/signup",
        json={
            "username": "Rival",
            "email": "rival-opening@shop.test",
            "phone": "9990000202",
            "password": "rivalpass1",
        },
    )
    headers = {"Authorization": f"Bearer {other.json()['token']}"}

    response = client.put(
        f"/api/customers/{customer_id}/opening-balance",
        headers=headers,
        json={"amount": 1, "balance_type": "due"},
    )
    assert response.status_code == 404
