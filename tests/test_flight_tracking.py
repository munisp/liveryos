"""Flight tracking tests — parse normalization, hysteresis, lock window,
cancellation proposals, stale-data discipline, provenance honesty."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "livery_core"))

from livery_core.adapters.flights import (  # noqa: E402
    FlightSnapshot, FlightStatus, TrackerPolicy, adjust_pickup,
    parse_feed_snapshot,
)

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
SCHED = NOW + timedelta(hours=3)
POLICY = TrackerPolicy()


def snap(status=FlightStatus.SCHEDULED, est=None, provenance="feed",
         observed=None):
    return FlightSnapshot("TP1311", status, SCHED, est,
                          observed or NOW, provenance)


# ---------- parsing ----------

def test_parse_normalizes_aeroapi_shapes():
    s = parse_feed_snapshot("tp1311", {
        "status": "Delayed",
        "scheduled_arrival": "2026-11-21T15:00:00Z",
        "estimated_arrival": "2026-11-21T15:40:00Z",
    }, observed_at=NOW)
    assert s.flight == "TP1311"
    assert s.status == FlightStatus.DELAYED
    assert s.estimated_arrival == SCHED + timedelta(minutes=40)


def test_unknown_status_quarantines_not_guesses():
    s = parse_feed_snapshot("TP1311", {
        "status": "holding_pattern_wobble",
        "scheduled_arrival": "2026-11-21T15:00:00Z",
    }, observed_at=NOW)
    assert s.status == FlightStatus.UNKNOWN


def test_delay_without_estimate_downgrades_to_unknown():
    s = parse_feed_snapshot("TP1311", {
        "status": "delayed",
        "scheduled_arrival": "2026-11-21T15:00:00Z",
        # no estimated arrival — claim is not actionable
    }, observed_at=NOW)
    assert s.status == FlightStatus.UNKNOWN


def test_naive_feed_datetimes_rejected():
    with pytest.raises(ValueError, match="tz-aware"):
        parse_feed_snapshot("TP1311", {
            "status": "scheduled",
            "scheduled_arrival": datetime(2026, 11, 21, 15, 0),  # naive
        }, observed_at=NOW)


# ---------- adjustment policy ----------

def test_delay_shifts_pickup_by_estimate_plus_buffer():
    est = SCHED + timedelta(minutes=40)
    pickup = SCHED + POLICY.curb_buffer
    adj, ev = adjust_pickup(booking_ref="SO-9", snapshot=snap(
        FlightStatus.DELAYED, est), current_pickup_at=pickup,
        policy=POLICY, now=NOW)
    assert adj.action == "shift"
    assert adj.new_pickup_at == est + POLICY.curb_buffer
    assert ev.type == "trip.rebooked"
    assert adj.provenance == "feed"


def test_small_wobble_does_not_move_pickup():
    est = SCHED + timedelta(minutes=5)   # below 10-min hysteresis
    pickup = SCHED + POLICY.curb_buffer
    adj, ev = adjust_pickup(booking_ref="SO-9", snapshot=snap(
        FlightStatus.DELAYED, est), current_pickup_at=pickup,
        policy=POLICY, now=NOW)
    assert adj.action == "none"
    assert "hysteresis" in adj.reason
    assert ev is None


def test_inside_lock_window_alerts_instead_of_shifting():
    est = SCHED + timedelta(minutes=40)
    pickup = NOW + timedelta(minutes=30)  # inside 45-min lock window
    adj, ev = adjust_pickup(booking_ref="SO-9", snapshot=snap(
        FlightStatus.DELAYED, est), current_pickup_at=pickup,
        policy=POLICY, now=NOW)
    assert adj.action == "alert"
    assert adj.new_pickup_at is None     # human decides
    assert "lock window" in adj.reason
    assert ev.payload["locked"] is True


def test_cancelled_flight_proposes_rebook_never_silent_cancel():
    adj, ev = adjust_pickup(booking_ref="SO-9", snapshot=snap(
        FlightStatus.CANCELLED), current_pickup_at=SCHED, policy=POLICY,
        now=NOW)
    assert adj.action == "rebook_proposal"
    assert "NOT auto-cancelled" in adj.reason
    assert ev.type == "flight.status_changed"


def test_stale_snapshot_ignored():
    old = NOW - timedelta(minutes=30)   # older than 15-min max age
    adj, ev = adjust_pickup(booking_ref="SO-9", snapshot=snap(
        FlightStatus.DELAYED, SCHED + timedelta(hours=1), observed=old),
        current_pickup_at=SCHED, policy=POLICY, now=NOW)
    assert adj.action == "none"
    assert "older than" in adj.reason


def test_no_provenance_means_no_touch():
    adj, ev = adjust_pickup(booking_ref="SO-9", snapshot=snap(
        FlightStatus.DELAYED, SCHED + timedelta(hours=1), provenance="none"),
        current_pickup_at=SCHED, policy=POLICY, now=NOW)
    assert adj.action == "none"
    assert "untouched" in adj.reason


def test_early_flight_can_pull_pickup_forward():
    est = SCHED - timedelta(minutes=25)
    pickup = SCHED + POLICY.curb_buffer
    adj, ev = adjust_pickup(booking_ref="SO-9", snapshot=snap(
        FlightStatus.EARLY, est), current_pickup_at=pickup,
        policy=POLICY, now=NOW)
    assert adj.action == "shift"
    assert adj.new_pickup_at < pickup
