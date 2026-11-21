"""Dispatch board tests — assignment engine (report Ch. 3 §3.2.2)."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "livery_core"))

from livery_core.dispatch.board import (  # noqa: E402
    Board, BoardRide, DriverState, auto_assign, estimate_eta_minutes,
    haversine_km, suggest_assignments,
)

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
# Lisbon pickup near Praça do Comércio
RIDE = BoardRide("ride-1", "sedan", 38.7075, -9.1364, NOW)


def drv(did, vclass="sedan", ok=True, idle=True, lat=38.71, lng=-9.14):
    return DriverState(did, vclass, ok, idle, lat, lng, NOW)


def test_haversine_lisbon_to_lagos():
    d = haversine_km(38.72, -9.14, 6.45, 3.39)
    assert 3700 < d < 3900  # ~3,790 km


def test_eta_estimate_is_conservative_and_labeled():
    assert estimate_eta_minutes(0) == 1
    assert estimate_eta_minutes(14) == 30  # 14km @ 28km/h = 30 min
    assert estimate_eta_minutes(28.1) >= 60


def test_hard_filters_exclude_wrong_class_noncompliant_busy():
    drivers = [
        drv("ok-1"),
        drv("van", vclass="van"),
        drv("expired", ok=False),
        drv("busy", idle=False),
    ]
    sugg = suggest_assignments(RIDE, drivers)
    assert [s.driver_id for s in sugg] == ["ok-1"]


def test_proximity_ranking_nearest_first():
    drivers = [
        drv("far", lat=38.80, lng=-9.00),     # ~13 km
        drv("near", lat=38.708, lng=-9.137),  # ~70 m
        drv("mid", lat=38.73, lng=-9.15),     # ~2.7 km
    ]
    sugg = suggest_assignments(RIDE, drivers)
    assert [s.driver_id for s in sugg] == ["near", "mid", "far"]
    assert sugg[0].eta_source == "estimate"  # never claims routed


def test_driver_without_fix_goes_last_with_none_distance():
    nofix = DriverState("nofix", "sedan", True, True, None, None, None)
    sugg = suggest_assignments(RIDE, [nofix, drv("withfix")])
    assert sugg[-1].driver_id == "nofix"
    assert sugg[-1].distance_km is None and sugg[-1].eta_source == "none"


def test_auto_assign_nearest_within_cap():
    res = auto_assign(RIDE, [drv("far", lat=38.80, lng=-9.00), drv("near", lat=38.708, lng=-9.137)])
    assert res.assigned and res.driver_id == "near"
    assert "estimate" in res.reason


def test_auto_assign_refuses_beyond_cap_and_no_fix():
    far_only = [drv("far", lat=38.90, lng=-8.80)]  # ~37 km
    res = auto_assign(RIDE, far_only, max_distance_km=15.0)
    assert not res.assigned and "cap" in res.reason
    nofix = [DriverState("n", "sedan", True, True, None, None, None)]
    res2 = auto_assign(RIDE, nofix)
    assert not res.assigned and "no position fix" in res2.reason


def test_auto_assign_explains_empty_fleet():
    res = auto_assign(RIDE, [drv("van", vclass="van"), drv("busy", idle=False)])
    assert not res.assigned
    assert "vehicle class" in res.reason and "active ride" in res.reason


def test_board_tick_assigns_and_marks_driver_busy():
    board = Board(unplaced=[RIDE], fleet={"d1": drv("d1")})
    results = board.tick()
    assert results["ride-1"].assigned
    assert not board.unplaced                      # placed rides leave the board
    assert not board.fleet["d1"].idle              # committed driver is non-idle next tick
    # second ride cannot take the same driver
    ride2 = BoardRide("ride-2", "sedan", 38.71, -9.14, NOW)
    board.unplaced.append(ride2)
    res2 = board.tick()
    assert not res2["ride-2"].assigned
