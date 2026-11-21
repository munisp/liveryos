"""Ride transition-map tests + package import sanity."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "livery_core"))

from livery_core.dispatch.doctype.livery_ride.livery_ride import can_transition  # noqa: E402


def test_happy_path():
    for a, b in [("QUOTED", "ASSIGNED"), ("ASSIGNED", "EN_ROUTE"),
                 ("EN_ROUTE", "AT_PICKUP"), ("AT_PICKUP", "IN_PROGRESS"),
                 ("IN_PROGRESS", "COMPLETED"), ("COMPLETED", "INVOICED")]:
        assert can_transition(a, b)


def test_terminal_states_absorbing():
    for s in ("INVOICED", "CANCELLED", "NO_SHOW"):
        for t in ("QUOTED", "ASSIGNED", "COMPLETED"):
            assert not can_transition(s, t)


def test_no_skipping_dispatch_steps():
    assert not can_transition("QUOTED", "IN_PROGRESS")
    assert not can_transition("ASSIGNED", "IN_PROGRESS")
    assert not can_transition("EN_ROUTE", "COMPLETED")


def test_no_show_only_from_assigned_or_at_pickup():
    assert can_transition("ASSIGNED", "NO_SHOW")
    assert can_transition("AT_PICKUP", "NO_SHOW")
    assert not can_transition("EN_ROUTE", "NO_SHOW")


def test_requeue_from_assigned_only():
    """Board requeue (driver fell through) returns the ride to QUOTED —
    legal only from ASSIGNED, never mid-trip."""
    assert can_transition("ASSIGNED", "QUOTED")
    assert not can_transition("EN_ROUTE", "QUOTED")
    assert not can_transition("IN_PROGRESS", "QUOTED")
    assert not can_transition("COMPLETED", "QUOTED")
