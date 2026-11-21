"""Affiliate engine tests — the moat component (report Ch. 3 §3.2.6)."""

import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "livery_core"))

from livery_core.affiliates.engine import (  # noqa: E402
    BlockingRule, FarmRequest, Partner, check_eligibility, select_partner,
    settle_farm_in, settle_farm_out, split_minor,
)

REQ = FarmRequest("BK-1", "acme", "sedan", 14550, "EUR",
                  pickup_airport="LIS", pickup_zone="lisbon-center")
LAGOS_REQ = FarmRequest("BK-2", "lagos-op", "suv", 5_000_000, "NGN",
                        pickup_airport="LOS", pickup_zone="lagos-island")


def test_split_never_loses_a_penny():
    for amount in (1, 3, 99, 100, 101, 14550, 5_000_000, 999_999_999):
        for pct in (Decimal("10"), Decimal("12.5"), Decimal("33.33"), Decimal("50"), Decimal("87.5")):
            p, o = split_minor(amount, pct)
            assert p + o == amount, (amount, pct, p, o)
            assert 0 <= p <= amount


def test_split_rounding_half_up():
    assert split_minor(101, Decimal("50")) == (51, 50)   # 50.5 → partner 51
    assert split_minor(100, Decimal("33.33")) == (33, 67)


def test_split_validates_inputs():
    with pytest.raises(ValueError):
        split_minor(-1, Decimal("50"))
    with pytest.raises(ValueError):
        split_minor(100, Decimal("0"))
    with pytest.raises(ValueError):
        split_minor(100, Decimal("100.01"))


def test_eligibility_direction_and_active():
    farm_in_only = Partner("p1", "farm_in", Decimal("20"))
    res = check_eligibility(REQ, farm_in_only, direction="farm_out")
    assert not res.eligible and "farm-out" in res.reason
    inactive = Partner("p2", "both", Decimal("20"), active=False)
    assert not check_eligibility(REQ, inactive).eligible


def test_blocking_rules_airport_zone_class():
    blocked_lis = Partner("p", "both", Decimal("20"),
                          blocked=(BlockingRule(airport="LIS"),))
    res = check_eligibility(REQ, blocked_lis)
    assert not res.eligible and "airport=LIS" in res.reason

    blocked_zone = Partner("p", "both", Decimal("20"),
                           blocked=(BlockingRule(zone="lagos-island"),))
    assert not check_eligibility(LAGOS_REQ, blocked_zone).eligible
    assert check_eligibility(REQ, blocked_zone).eligible

    blocked_class = Partner("p", "both", Decimal("20"),
                            blocked=(BlockingRule(service_class="suv"),))
    assert not check_eligibility(LAGOS_REQ, blocked_class).eligible
    assert check_eligibility(REQ, blocked_class).eligible


def test_select_partner_prefers_dispatcher_choice():
    a = Partner("alpha", "both", Decimal("20"))
    b = Partner("beta", "both", Decimal("15"))
    chosen, results = select_partner(REQ, [a, b], preferred_ref="beta")
    assert chosen.partner_ref == "beta"
    # without preference: configured split beats none, then ref order
    no_split = Partner("gamma", "both", None)
    chosen2, _ = select_partner(REQ, [no_split, a, b])
    assert chosen2.partner_ref == "alpha"


def test_select_partner_returns_all_rejection_reasons():
    a = Partner("alpha", "farm_in", Decimal("20"))
    b = Partner("beta", "both", Decimal("15"), active=False)
    chosen, results = select_partner(REQ, [a, b])
    assert chosen is None
    assert len(results) == 2 and all(not r.eligible for r in results)


def test_settle_farm_out_money_flow():
    p = Partner("beta", "both", Decimal("20"))
    s = settle_farm_out(REQ, p)
    assert s.partner_minor == 2910 and s.operator_minor == 11640
    assert s.partner_minor + s.operator_minor == REQ.amount_minor
    assert s.currency == "EUR" and s.direction == "farm_out"
    assert s.engine_version  # provenance for Ch. 8 reconciliation


def test_settle_farm_out_per_booking_quote_is_honestly_none():
    p = Partner("gamma", "both", None)
    s = settle_farm_out(REQ, p)
    assert s.partner_minor is None and s.operator_minor is None
    assert s.split_pct is None  # never fabricate a zero


def test_settle_farm_in_our_share():
    p = Partner("origin", "farm_in", None)
    s = settle_farm_in(LAGOS_REQ, p, Decimal("75"))
    assert s.partner_minor == 3_750_000        # our share
    assert s.operator_minor == 1_250_000       # originator keeps
    assert s.partner_minor + s.operator_minor == LAGOS_REQ.amount_minor
    assert s.currency == "NGN"
