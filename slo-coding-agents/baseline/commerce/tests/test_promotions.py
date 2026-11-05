import datetime

import pytest

from services.promotions.coupons import parse_expiry


def _validate(promotions, code, subtotal_pence=10000):
    return promotions.post("/internal/coupons/validate", json={"code": code, "subtotal_pence": subtotal_pence})


def test_get_coupon(promotions):
    data = promotions.get("/internal/coupons/WELCOME10").get_json()
    assert data["percent_off"] == 10
    assert data["min_spend_pence"] == 0
    assert promotions.get("/internal/coupons/NOPE").status_code == 404


def test_validate_percent_coupon(promotions):
    r = _validate(promotions, "WELCOME10")
    assert r.status_code == 200
    assert r.get_json() == {"code": "WELCOME10", "percent_off": 10, "free_delivery": False}


def test_code_case_insensitive(promotions):
    assert _validate(promotions, "larder5").status_code == 200


def test_iso_timestamp_coupon(promotions):
    assert _validate(promotions, "SPRING15").status_code == 200


def test_unknown_coupon(promotions):
    r = _validate(promotions, "NOPE")
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_coupon"


def test_expired_coupon(promotions):
    r = _validate(promotions, "SUMMER25")
    assert r.status_code == 400
    assert r.get_json()["error"] == "expired_coupon"


def test_min_spend(promotions):
    r = _validate(promotions, "FAMILY12", 249)
    assert r.status_code == 400
    assert r.get_json() == {"error": "min_spend", "min_spend_pence": 4000}


def test_parse_expiry_formats():
    d = parse_expiry("2027-06-30")
    assert (d.year, d.month, d.day) == (2027, 6, 30)
    d = parse_expiry("2027-04-30T23:59:59Z")
    assert d.tzinfo is not None
    assert parse_expiry(None) is None
    assert parse_expiry("none") is None


def test_parse_expiry_legacy_spreadsheet_format():
    d = parse_expiry("31/Dec/2016")
    assert d == datetime.datetime(2016, 12, 31, 23, 59, 59, tzinfo=datetime.timezone.utc)
