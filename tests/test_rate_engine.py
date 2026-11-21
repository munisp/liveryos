"""Rate engine tests — the critical path gets the densest coverage
(report Ch. 3 §3.2.1; acceptance = parity replay, Gate 3)."""

import sys
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "livery_core"))

from livery_core.rate_engine.engine import (  # noqa: E402
    QuoteContext, RateRule, RuleModel, Zone, apply_override, Override,
    parity_replay, price,
)

LISBON = Zone("lisbon", ((38.7, -9.2), (38.8, -9.2), (38.8, -9.1), (38.7, -9.1)))
LAGOS = Zone("lagos", ((6.4, 3.3), (6.6, 3.3), (6.6, 3.5), (6.4, 3.5)))


def ctx(**kw):
    base = dict(
        tenant="demo", currency="EUR", service_class="sedan", base_minor=9000,
        pickup_at=datetime(2026, 6, 15, 10, 0),  # Monday
        pickup_lat=38.75, pickup_lng=-9.15, lead_minutes=120,
    )
    base.update(kw)
    return QuoteContext(**base)


def test_multiplier_rule_applies():
    r = RateRule("peak-1.5x", RuleModel.MULTIPLIER, Decimal("1.5"), zones=(LISBON,))
    q = price(ctx(), [r])
    assert q.final_minor == 13500
    assert q.applied[0].rule == "peak-1.5x"


def test_fixed_rule_replaces_base():
    r = RateRule("airport-flat", RuleModel.FIXED, Decimal("12000"))
    q = price(ctx(), [r])
    assert q.final_minor == 12000


def test_percentage_surcharge_and_discount():
    up = RateRule("weekend+20", RuleModel.PERCENTAGE, Decimal("20"))
    down = RateRule("corp-10", RuleModel.PERCENTAGE, Decimal("-10"))
    assert price(ctx(), [up]).final_minor == 10800
    assert price(ctx(), [down]).final_minor == 8100


def test_zone_miss_no_match():
    r = RateRule("lisbon-only", RuleModel.MULTIPLIER, Decimal("2"), zones=(LAGOS,))
    assert price(ctx(), [r]).final_minor == 9000  # pickup is in Lisbon, not Lagos


def test_zone_match_lagos():
    r = RateRule("lagos-surge", RuleModel.MULTIPLIER, Decimal("1.25"), zones=(LAGOS,))
    q = price(ctx(pickup_lat=6.5, pickup_lng=3.4, currency="NGN"), [r])
    assert q.final_minor == 11250


def test_overnight_time_window():
    r = RateRule("night", RuleModel.PERCENTAGE, Decimal("25"),
                 time_window=(time(22, 0), time(5, 0)))
    assert price(ctx(pickup_at=datetime(2026, 6, 15, 23, 30)), [r]).final_minor == 11250
    assert price(ctx(pickup_at=datetime(2026, 6, 15, 10, 0)), [r]).final_minor == 9000


def test_weekday_and_date_rules():
    mon = RateRule("monday", RuleModel.PERCENTAGE, Decimal("10"), weekdays=(0,))
    xmas = RateRule("dec25", RuleModel.FIXED, Decimal("20000"), dates=(date(2026, 12, 25),))
    assert price(ctx(), [mon]).final_minor == 9900
    assert price(ctx(pickup_at=datetime(2026, 12, 25, 10, 0)), [xmas]).final_minor == 20000


def test_lead_time_bands():
    last_minute = RateRule("rush", RuleModel.PERCENTAGE, Decimal("30"), max_lead_minutes=60)
    advance = RateRule("early", RuleModel.PERCENTAGE, Decimal("-5"), min_lead_minutes=2880)
    assert price(ctx(lead_minutes=30), [last_minute]).final_minor == 11700
    assert price(ctx(lead_minutes=120), [last_minute]).final_minor == 9000
    assert price(ctx(lead_minutes=5000), [advance]).final_minor == 8550


def test_priority_and_non_stackable_termination():
    low = RateRule("a", RuleModel.PERCENTAGE, Decimal("50"), priority=1)
    high = RateRule("b", RuleModel.PERCENTAGE, Decimal("10"), priority=10)
    stackable_high = RateRule("b", RuleModel.PERCENTAGE, Decimal("10"), priority=10, stackable=True)
    assert price(ctx(), [low, high]).final_minor == 9900          # high wins, ends
    assert price(ctx(), [low, stackable_high]).final_minor == 14850  # both apply


def test_rounding_half_up():
    r = RateRule("odd", RuleModel.MULTIPLIER, Decimal("1.005"))
    assert price(ctx(), [r]).final_minor == 9045  # 9000 * 1.005 = 9045.0 exact
    r2 = RateRule("odd2", RuleModel.PERCENTAGE, Decimal("0.0055556"))
    q = price(ctx(base_minor=9001), [r2])
    assert isinstance(q.final_minor, int)


def test_override_audited_and_validated():
    q = price(ctx(), [])
    o = Override(actor="dispatcher-7", at=datetime(2026, 6, 14, 9, 0),
                 final_minor=8500, reason="regular client goodwill")
    q = apply_override(q, o)
    assert q.overridden and q.final_minor == 8500
    with pytest.raises(ValueError):
        apply_override(price(ctx(), []), Override("d", datetime.now(), 0, "x"))
    with pytest.raises(ValueError):
        apply_override(price(ctx(), []), Override("d", datetime.now(), 100, "  "))


def test_parity_replay_gate_math():
    history = [(ctx(), 9000), (ctx(), 9100), (ctx(), 20000)]
    rules = []
    res = parity_replay(history, rules, tolerance_pct=Decimal("2"))
    # exact: 1 (9000); within 2%: 9100 is 1.1% off -> within; 20000 -> miss
    assert res["exact"] == 1 and res["within_tolerance"] == 1 and res["misses"] == [2]
    assert res["gate_rate"] == pytest.approx(2 / 3)
    # mark the miss as an explained override variance -> gate passes fully
    res2 = parity_replay(history, rules, explained_overrides={2})
    assert res2["gate_rate"] == 1.0 and res2["component_rate"] == pytest.approx(1 / 3)
