"""Booking widget + corporate portal domain tests — key issuance, origin
allowlist, rate limiting, PO policy, class caps, budgets, approvals."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "livery_core"))

from livery_core.widget import (  # noqa: E402
    CorporateAccount, CorporatePolicyError, CostCenter, KeyStore,
    RateLimitExceeded, TravelerProfile, WidgetOriginError, evaluate_booking,
    issue_key, validate_request,
)

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


# ---------- widget keys ----------

def test_issue_and_validate_happy_path():
    ks = KeyStore()
    key, raw = issue_key(ks, tenant="hotel-abc",
                         allowed_origins=("https://hotel.example.pt",))
    got = validate_request(ks, raw_key=raw,
                           origin="https://hotel.example.pt", now=NOW)
    assert got.key_id == key.key_id
    assert raw not in repr(key)  # raw key never stored


def test_unknown_key_rejected():
    with pytest.raises(WidgetOriginError):
        validate_request(KeyStore(), raw_key="lvk_nope",
                         origin="https://x.example", now=NOW)


def test_wrong_origin_rejected_even_with_valid_key():
    ks = KeyStore()
    _, raw = issue_key(ks, tenant="t", allowed_origins=("https://a.example",))
    with pytest.raises(WidgetOriginError, match="not allowed"):
        validate_request(ks, raw_key=raw, origin="https://evil.example",
                         now=NOW)


def test_revoked_key_dead_immediately():
    ks = KeyStore()
    key, raw = issue_key(ks, tenant="t", allowed_origins=("https://a.example",))
    ks.revoke(key, now=NOW)
    with pytest.raises(WidgetOriginError, match="revoked|unknown"):
        validate_request(ks, raw_key=raw, origin="https://a.example", now=NOW)


def test_rate_limit_trips_and_recovers():
    ks = KeyStore()
    _, raw = issue_key(ks, tenant="t", allowed_origins=("https://a.example",),
                       rate_per_minute=2)
    validate_request(ks, raw_key=raw, origin="https://a.example", now=NOW)
    validate_request(ks, raw_key=raw, origin="https://a.example", now=NOW)
    with pytest.raises(RateLimitExceeded):
        validate_request(ks, raw_key=raw, origin="https://a.example", now=NOW)
    # one minute later the bucket has refilled
    validate_request(ks, raw_key=raw, origin="https://a.example",
                     now=NOW + timedelta(minutes=1))


def test_any_origin_key_refused_at_issuance():
    with pytest.raises(ValueError, match="at least one allowed origin"):
        issue_key(KeyStore(), tenant="t", allowed_origins=())


def test_http_origin_refused_except_localhost():
    with pytest.raises(ValueError, match="https"):
        issue_key(KeyStore(), tenant="t",
                  allowed_origins=("http://hotel.example",))
    issue_key(KeyStore(), tenant="t",
              allowed_origins=("http://localhost:8080",))  # dev exempt


# ---------- corporate policy ----------

def _account(**kw):
    base = dict(
        account_id="C-1", tenant="pt-demo", name="TechLisboa", currency="EUR",
        po_required=True, require_approval_over_minor=10000,
        cost_centers=(CostCenter("SALES", "Sales", 50000, 40000),
                      CostCenter("OPS", "Ops", None, 0)),
        travelers=(TravelerProfile("T-1", "Ana", "ana@x.pt",
                                   default_cost_center="SALES",
                                   max_service_class="business"),
                   TravelerProfile("T-2", "Rui", "rui@x.pt",
                                   self_book=False)),
    )
    base.update(kw)
    return CorporateAccount(**base)


def test_happy_path_within_policy():
    d = evaluate_booking(_account(), traveler_id="T-1",
                         service_class="business", amount_minor=8000,
                         cost_center_code="SALES", po_number="PO-77",
                         currency="EUR")
    assert d.approved and not d.needs_approval


def test_missing_po_hard_rejected():
    with pytest.raises(CorporatePolicyError, match="PO number"):
        evaluate_booking(_account(), traveler_id="T-1",
                         service_class="business", amount_minor=8000,
                         cost_center_code="SALES", po_number=" ",
                         currency="EUR")


def test_class_cap_enforced():
    with pytest.raises(CorporatePolicyError, match="exceeds traveler cap"):
        evaluate_booking(_account(), traveler_id="T-1",
                         service_class="executive", amount_minor=8000,
                         cost_center_code="SALES", po_number="PO-1",
                         currency="EUR")


def test_budget_overflow_rejected_with_numbers():
    with pytest.raises(CorporatePolicyError, match="budget would be exceeded"):
        evaluate_booking(_account(), traveler_id="T-1",
                         service_class="business", amount_minor=15000,
                         cost_center_code="SALES", po_number="PO-1",
                         currency="EUR")


def test_uncapped_center_never_budget_rejects():
    d = evaluate_booking(_account(), traveler_id="T-1",
                         service_class="business", amount_minor=900000,
                         cost_center_code="OPS", po_number="PO-1",
                         currency="EUR")
    assert d.needs_approval  # over approval threshold instead


def test_currency_mismatch_rejected_never_blended():
    with pytest.raises(CorporatePolicyError, match="never blends"):
        evaluate_booking(_account(), traveler_id="T-1",
                         service_class="business", amount_minor=8000,
                         cost_center_code="SALES", po_number="PO-1",
                         currency="NGN")


def test_self_book_false_rejected():
    with pytest.raises(CorporatePolicyError, match="may not self-book"):
        evaluate_booking(_account(), traveler_id="T-2",
                         service_class="economy", amount_minor=1000,
                         cost_center_code="OPS", po_number="PO-1",
                         currency="EUR")


def test_over_threshold_routes_to_approval():
    d = evaluate_booking(_account(), traveler_id="T-1",
                         service_class="business", amount_minor=12000,
                         cost_center_code="OPS", po_number="PO-1",
                         currency="EUR")
    assert d.needs_approval and d.approver == "travel-manager"
    assert not d.approved


def test_inactive_account_rejected():
    with pytest.raises(CorporatePolicyError, match="inactive"):
        evaluate_booking(_account(active=False), traveler_id="T-1",
                         service_class="business", amount_minor=1000,
                         cost_center_code="OPS", po_number="PO-1",
                         currency="EUR")
