"""Reconciliation tests — nightly parallel-run verdict + cutover gate (Ch. 8 §8.4)."""

import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "livery_core"))

from livery_core.adapters.transition.ga_adapter import CanonicalBooking  # noqa: E402
from livery_core.migration.reconcile import (  # noqa: E402
    cutover_gate, reconcile_bookings,
)

DAY = date(2026, 11, 21)
PICKUP = datetime(2026, 11, 25, 9, 30, tzinfo=timezone.utc)


def bk(ext_id, minor=14550, status="QUOTED", ccy="EUR"):
    return CanonicalBooking(ext_id, "Hotel Avenida", "Maria Silva", PICKUP,
                            "LIS Airport", "Rua Augusta 24", "sedan",
                            status, minor, ccy)


def test_clean_night_passes_with_native_bookings_explained():
    ga = [bk("GA-1"), bk("GA-2")]
    lv = [bk("GA-1"), bk("GA-2"), bk("NATIVE-1")]   # direct-channel booking
    rep = reconcile_bookings(ga, lv, "acme", DAY)
    assert rep.matched == 2
    assert len(rep.by_kind("missing_in_ga")) == 1
    assert rep.clean  # native bookings are explained, not failures


def test_missing_in_livery_fails_the_night():
    rep = reconcile_bookings([bk("GA-1"), bk("GA-2")], [bk("GA-1")], "acme", DAY)
    assert not rep.clean
    d = rep.by_kind("missing_in_livery")[0]
    assert d.external_id == "GA-2" and "Maria Silva" in d.detail


def test_money_mismatch_exact_by_default():
    ga = [bk("GA-1", minor=14550)]
    lv = [bk("GA-1", minor=14500)]
    rep = reconcile_bookings(ga, lv, "acme", DAY)
    assert not rep.clean
    d = rep.by_kind("money_mismatch")[0]
    assert d.ga_value == "14550" and d.livery_value == "14500"
    # with an explicit tolerance the same pair passes
    rep2 = reconcile_bookings(ga, lv, "acme", DAY, money_tolerance_minor=50)
    assert rep2.clean


def test_status_mismatch_flagged():
    rep = reconcile_bookings([bk("GA-1", status="ASSIGNED")],
                             [bk("GA-1", status="QUOTED")], "acme", DAY)
    assert not rep.clean and rep.by_kind("status_mismatch")


def test_cutover_gate_requires_consecutive_clean_nights():
    clean = [reconcile_bookings([bk("GA-1")], [bk("GA-1")], "acme",
                                date(2026, 11, d)) for d in range(10, 17)]
    gate = cutover_gate(clean, required_clean_nights=7)
    assert gate["go"] and gate["clean_nights"] == 7

    dirty = clean[:-1] + [reconcile_bookings([bk("GA-1")], [], "acme", DAY)]
    gate2 = cutover_gate(dirty, required_clean_nights=7)
    assert not gate2["go"] and gate2["blocking"]

    too_short = cutover_gate(clean[:5], required_clean_nights=7)
    assert not too_short["go"] and too_short["observed_nights"] == 5
