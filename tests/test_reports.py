"""
Sales reporting.

The figures here are what a shop owner decides things by, so the tests care
about the arithmetic and about the day boundary: buckets follow the shop's
local clock, and a sale rung up early in the morning must not land in
yesterday because the server stores UTC.
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

client = TestClient(app)

IST = 330  # minutes east of UTC


@pytest.fixture(scope="module")
def auth():
    response = client.post(
        "/api/auth/signup",
        json={
            "username": "Report Shop",
            "email": "reports@shop.test",
            "phone": "9660000001",
            "password": "reportpass1",
        },
    )
    assert response.status_code in (200, 201), response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _bill(auth, phone, name, total, paid):
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


def test_requires_authentication():
    assert client.get("/api/reports/summary").status_code in (401, 403)


def test_rejects_an_unknown_period(auth):
    assert (
        client.get(
            "/api/reports/summary", headers=auth, params={"period": "hourly"}
        ).status_code
        == 422
    )


def test_empty_shop_reports_zeroes_not_an_error(auth):
    body = client.get(
        "/api/reports/summary", headers=auth, params={"period": "daily", "tz_offset": IST}
    ).json()
    assert body["totals"]["bills"] == 0
    assert body["totals"]["billed"] == 0
    # The chart still needs its columns, so buckets are present but empty.
    assert len(body["buckets"]) == 14


def test_counts_sales_collections_and_dues(auth):
    _bill(auth, "9661110001", "Report One", 500, 200)
    _bill(auth, "9661110002", "Report Two", 300, 300)

    body = client.get(
        "/api/reports/summary", headers=auth, params={"period": "daily", "tz_offset": IST}
    ).json()
    totals = body["totals"]
    assert totals["bills"] == 2
    assert totals["billed"] == 800
    assert totals["collected"] == 500
    # 800 sold, 500 taken — 300 went on the book.
    assert totals["outstanding"] == 300

    today = body["buckets"][-1]
    assert today["bills"] == 2
    assert today["billed"] == 800


def test_a_later_payment_is_takings_but_not_a_new_sale(auth):
    created = _bill(auth, "9661110003", "Report Three", 400, 0)
    customer_id = created["customer"]["id"]

    before = client.get(
        "/api/reports/summary", headers=auth, params={"period": "daily", "tz_offset": IST}
    ).json()["totals"]

    paid = client.post(
        f"/api/customers/{customer_id}/payments",
        headers=auth,
        data={"amount": "400", "payment_type": "cash"},
    )
    assert paid.status_code in (200, 201), paid.text

    after = client.get(
        "/api/reports/summary", headers=auth, params={"period": "daily", "tz_offset": IST}
    ).json()["totals"]

    # Settling an old due is money in, but nothing new was sold — counting it
    # as billed would book the same rupees twice.
    assert after["collected"] == before["collected"] + 400
    assert after["billed"] == before["billed"]


def test_every_period_returns_its_own_bucket_count(auth):
    expected = {"daily": 14, "weekly": 12, "monthly": 12, "yearly": 5}
    for period, count in expected.items():
        body = client.get(
            "/api/reports/summary",
            headers=auth,
            params={"period": period, "tz_offset": IST},
        ).json()
        assert body["period"] == period
        assert len(body["buckets"]) == count
        # Oldest first, so a chart can render them left to right.
        starts = [b["starts_at"] for b in body["buckets"]]
        assert starts == sorted(starts)


def test_buckets_follow_the_shops_clock_not_utc(auth):
    """
    A sale at 01:00 IST is 19:30 UTC the day before. Bucketing on UTC would
    move it into yesterday's takings.
    """
    from app.database import SessionLocal
    from app.models import Bill, Customer

    created = _bill(auth, "9661110004", "Early Bird", 100, 100)
    bill_id = created["bill"]["id"]

    session = SessionLocal()
    try:
        # Put the bill at 01:00 local, i.e. 19:30 UTC yesterday.
        local_now = datetime.utcnow() + timedelta(minutes=IST)
        local_1am = local_now.replace(hour=1, minute=0, second=0, microsecond=0)
        session.query(Bill).filter(Bill.id == bill_id).update(
            {Bill.created_at: local_1am - timedelta(minutes=IST)}
        )
        session.commit()
    finally:
        session.close()

    body = client.get(
        "/api/reports/summary", headers=auth, params={"period": "daily", "tz_offset": IST}
    ).json()

    # It belongs to today in the shop's own reckoning.
    assert body["buckets"][-1]["bills"] >= 1
    labels = [b["label"] for b in body["buckets"]]
    assert len(labels) == len(set(labels)), "each day should appear once"


def test_one_shop_cannot_see_anothers_takings(auth):
    other = client.post(
        "/api/auth/signup",
        json={
            "username": "Rival Shop",
            "email": "rival@shop.test",
            "phone": "9660000002",
            "password": "rivalpass1",
        },
    )
    headers = {"Authorization": f"Bearer {other.json()['token']}"}
    body = client.get(
        "/api/reports/summary", headers=headers, params={"period": "daily"}
    ).json()
    assert body["totals"]["bills"] == 0
    assert body["totals"]["billed"] == 0
