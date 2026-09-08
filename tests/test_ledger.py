"""
The customer ledger, case by case.

These are the scenarios a shop actually hits, and the arithmetic decides what
a shop believes it is owed — so each case pins down every figure: what the
bill was worth, what was received, what credit was drawn down, what was left
owing, and what credit remains.

The bill stores its own breakdown, so a receipt reprinted later shows what
happened at the time rather than today's balances. These tests assert the
stored figures, not a recomputation of them.
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

_phone_counter = iter(range(1000, 9999))


@pytest.fixture(scope="module")
def auth():
    response = client.post(
        "/api/auth/signup",
        json={
            "username": "Ledger Shop",
            "email": "ledger@shop.test",
            "phone": "9880000001",
            "password": "ledgerpass1",
        },
    )
    assert response.status_code in (200, 201), response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _new_phone():
    return f"98811{next(_phone_counter):05d}"


def _bill(auth, phone, total, paid=None, payment_type="cash", name="Ledger Customer"):
    data = {
        "phone": phone,
        "customer_name": name,
        "items": f'[{{"item_name": "Item", "qty": 1, "rate": {total}}}]',
        "payment_type": payment_type,
    }
    if paid is not None:
        data["amount_paid"] = str(paid)
    if payment_type in ("online", "cheque"):
        data["transaction_number"] = "REF123"

    response = client.post("/api/bills", headers=auth, data=data)
    assert response.status_code in (200, 201), response.text
    return response.json()


def _advance(auth, customer_id, amount):
    response = client.post(
        f"/api/customers/{customer_id}/payments",
        headers=auth,
        data={"amount": str(amount), "payment_type": "cash"},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def _assert_bill(bill, *, total, paid, applied, outstanding, added):
    """Every figure on the bill, so a wrong one names itself."""
    assert bill["bill_total"] == total, "bill value"
    assert bill["amount_paid"] == paid, "money received"
    assert bill["advance_applied"] == applied, "credit drawn down"
    assert bill["unbalance"] == outstanding, "left owing on this bill"
    assert bill["advance_added"] == added, "credit created"
    # The bill's own arithmetic must balance: what it was worth equals what
    # settled it plus what is still owed.
    assert round(applied + (paid - added) + outstanding, 2) == total


# ── Case A — exact payment ───────────────────────────────────────────

def test_case_a_exact_payment(auth):
    phone = _new_phone()
    body = _bill(auth, phone, 1000, paid=1000)
    _assert_bill(body["bill"], total=1000, paid=1000, applied=0, outstanding=0, added=0)
    assert body["customer"]["total_unpaid"] == 0
    assert body["customer"]["advance_balance"] == 0


# ── Case B — partial payment ─────────────────────────────────────────

def test_case_b_partial_payment(auth):
    phone = _new_phone()
    body = _bill(auth, phone, 1000, paid=600)
    _assert_bill(body["bill"], total=1000, paid=600, applied=0, outstanding=400, added=0)
    assert body["customer"]["total_unpaid"] == 400
    assert body["customer"]["advance_balance"] == 0


# ── Case C — no payment, added to the account ────────────────────────

def test_case_c_pay_later_records_the_liability(auth):
    """
    Nothing changed hands, so this is not a cash payment of zero — it is its
    own kind, and the whole bill lands on the customer's account.
    """
    phone = _new_phone()
    body = _bill(auth, phone, 1000, payment_type="credit")
    _assert_bill(body["bill"], total=1000, paid=0, applied=0, outstanding=1000, added=0)
    assert body["bill"]["payment_type"] == "credit"
    assert body["customer"]["total_unpaid"] == 1000


# ── Case D — existing advance, smaller than the bill ─────────────────

def test_case_d_existing_advance_is_drawn_down_first(auth):
    phone = _new_phone()
    created = _bill(auth, phone, 100, paid=100)
    customer_id = created["customer"]["id"]
    _advance(auth, customer_id, 400)

    body = _bill(auth, phone, 1000, paid=600)
    _assert_bill(
        body["bill"], total=1000, paid=600, applied=400, outstanding=0, added=0
    )
    assert body["customer"]["advance_balance"] == 0
    assert body["customer"]["total_unpaid"] == 0


# ── Case E — advance greater than the bill ───────────────────────────

def test_case_e_advance_larger_than_the_bill_keeps_the_remainder(auth):
    phone = _new_phone()
    created = _bill(auth, phone, 100, paid=100)
    customer_id = created["customer"]["id"]
    _advance(auth, customer_id, 1500)

    body = _bill(auth, phone, 1000, paid=0)
    _assert_bill(body["bill"], total=1000, paid=0, applied=1000, outstanding=0, added=0)
    # Never more advance than the bill is worth; the rest stays as credit.
    assert body["customer"]["advance_balance"] == 500
    assert body["bill"]["advance_balance_after"] == 500


# ── Case F — overpayment becomes advance ─────────────────────────────

def test_case_f_overpayment_becomes_advance(auth):
    phone = _new_phone()
    body = _bill(auth, phone, 1000, paid=1300)
    _assert_bill(
        body["bill"], total=1000, paid=1300, applied=0, outstanding=0, added=300
    )
    assert body["customer"]["advance_balance"] == 300
    # The bill is worth 1000 however much was handed over.
    assert body["bill"]["bill_total"] == 1000


# ── Case G — advance plus an additional payment ──────────────────────

def test_case_g_advance_plus_new_payment(auth):
    phone = _new_phone()
    created = _bill(auth, phone, 100, paid=100)
    customer_id = created["customer"]["id"]
    _advance(auth, customer_id, 300)

    body = _bill(auth, phone, 1000, paid=700)
    _assert_bill(
        body["bill"], total=1000, paid=700, applied=300, outstanding=0, added=0
    )
    assert body["customer"]["advance_balance"] == 0
    assert body["customer"]["total_unpaid"] == 0


# ── Case H — advance settles the bill entirely ───────────────────────

def test_case_h_advance_settles_the_whole_bill(auth):
    """Nothing is received, so no payment type is meaningful."""
    phone = _new_phone()
    created = _bill(auth, phone, 100, paid=100)
    customer_id = created["customer"]["id"]
    _advance(auth, customer_id, 500)

    body = _bill(auth, phone, 500, payment_type="credit")
    _assert_bill(body["bill"], total=500, paid=0, applied=500, outstanding=0, added=0)
    assert body["customer"]["advance_balance"] == 0
    assert body["customer"]["total_unpaid"] == 0


# ── The account stays consistent across a long sequence ──────────────

def test_the_ledger_balances_after_many_transactions(auth):
    """
    A run of mixed activity, checked against the books at the end: everything
    billed equals everything settled plus everything still owed, and credit
    and debt are never held at once.
    """
    phone = _new_phone()
    created = _bill(auth, phone, 500, paid=500)
    customer_id = created["customer"]["id"]

    _bill(auth, phone, 1000, paid=600)          # 400 owed
    _advance(auth, customer_id, 1000)           # 400 settles, 600 credit
    _bill(auth, phone, 200, payment_type="credit")  # credit covers it
    _bill(auth, phone, 1000, paid=100)          # credit 400 + 100 cash
    _bill(auth, phone, 300, paid=800)           # overpays

    detail = client.get(f"/api/customers/{customer_id}", headers=auth).json()

    billed = round(sum(b["bill_total"] for b in detail["bills"]), 2)
    assert detail["total_amount"] == billed

    # The ledger identity. A bill records its state at the moment it was
    # written, and a later payment settles old dues without rewriting it —
    # so the books balance across bills AND payments together, never by
    # re-adding the bills' own outstanding figures.
    #
    #   billed = still owed + money received - credit not yet spent
    received = round(
        sum(b["amount_paid"] for b in detail["bills"])
        + sum(p["amount"] for p in detail["payments"]),
        2,
    )
    assert (
        round(detail["total_unpaid"] + received - detail["advance_balance"], 2)
        == billed
    ), (
        f"billed {billed}, owed {detail['total_unpaid']}, "
        f"received {received}, credit {detail['advance_balance']}"
    )

    assert detail["total_unpaid"] >= 0
    assert detail["advance_balance"] >= 0
    assert not (detail["total_unpaid"] > 0 and detail["advance_balance"] > 0)


def test_credit_bills_need_no_reference_or_amount(auth):
    """A bill nobody paid cannot carry a transaction number."""
    phone = _new_phone()
    response = client.post(
        "/api/bills",
        headers=auth,
        data={
            "phone": phone,
            "customer_name": "No Payment",
            "items": '[{"item_name": "Item", "qty": 1, "rate": 250}]',
            "payment_type": "credit",
        },
    )
    assert response.status_code in (200, 201), response.text
    assert response.json()["bill"]["unbalance"] == 250


def test_an_unknown_payment_type_is_still_refused(auth):
    response = client.post(
        "/api/bills",
        headers=auth,
        data={
            "phone": _new_phone(),
            "customer_name": "Bad Type",
            "items": '[{"item_name": "Item", "qty": 1, "rate": 100}]',
            "payment_type": "barter",
        },
    )
    assert response.status_code == 422
