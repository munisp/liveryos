"""Flight status parsing + pickup adjustment policy.

The feed fetcher is injected: production wires an AeroAPI-class HTTPS
client (FLIGHT_FEED / FLIGHT_FEED_KEY env), tests wire fakes. This module
only sees FlightSnapshot values — provider wire formats live at the edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from ..events import Event


class FlightStatus(str, Enum):
    SCHEDULED = "scheduled"
    EN_ROUTE = "en_route"
    DELAYED = "delayed"
    EARLY = "early"
    LANDED = "landed"
    CANCELLED = "cancelled"
    DIVERTED = "diverted"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FlightSnapshot:
    flight: str                       # IATA code, e.g. "TP1311"
    status: FlightStatus
    scheduled_arrival: datetime       # tz-aware UTC
    estimated_arrival: Optional[datetime]   # None when the feed has no estimate
    observed_at: datetime             # when the feed produced this snapshot
    provenance: str                   # "feed" | "manual" | "none"
    terminal: Optional[str] = None

    def is_stale(self, *, now: datetime, max_age: timedelta) -> bool:
        return (now - self.observed_at) > max_age


@dataclass(frozen=True)
class TrackerPolicy:
    min_shift: timedelta = timedelta(minutes=10)    # hysteresis threshold
    lock_window: timedelta = timedelta(minutes=45)  # human owns the last 45 min
    max_snapshot_age: timedelta = timedelta(minutes=15)
    # How long before arrival the driver should be wheels-at-curb.
    curb_buffer: timedelta = timedelta(minutes=15)


@dataclass(frozen=True)
class PickupAdjustment:
    """The decision record — emitted as an event, auditable, replayable."""
    booking_ref: str
    flight: str
    action: str                       # "shift" | "alert" | "rebook_proposal" | "none"
    old_pickup_at: Optional[datetime]
    new_pickup_at: Optional[datetime]
    reason: str
    provenance: str                   # carried from the snapshot — never "assumed"


def parse_feed_snapshot(flight: str, raw: dict,
                        *, observed_at: datetime) -> FlightSnapshot:
    """Normalize an AeroAPI-class record. Unknown statuses → UNKNOWN
    (quarantine discipline: we never map a guess onto a known state)."""
    def _dt(key: str) -> Optional[datetime]:
        v = raw.get(key)
        if not v:
            return None
        if isinstance(v, datetime):
            if v.tzinfo is None:
                raise ValueError(f"naive datetime in feed field {key} — "
                                 "feed times must be tz-aware (same rule as GPS fixes)")
            return v
        s = str(v).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            raise ValueError(f"naive datetime string in feed field {key}")
        return dt

    status_raw = str(raw.get("status", "")).lower()
    status_map = {
        "scheduled": FlightStatus.SCHEDULED,
        "enroute": FlightStatus.EN_ROUTE, "en_route": FlightStatus.EN_ROUTE,
        "delayed": FlightStatus.DELAYED,
        "early": FlightStatus.EARLY,
        "arrived": FlightStatus.LANDED, "landed": FlightStatus.LANDED,
        "cancelled": FlightStatus.CANCELLED, "canceled": FlightStatus.CANCELLED,
        "diverted": FlightStatus.DIVERTED,
    }
    status = status_map.get(status_raw, FlightStatus.UNKNOWN)
    scheduled = _dt("scheduled_arrival") or _dt("scheduled_in")
    if scheduled is None:
        raise ValueError("feed record has no scheduled arrival — cannot track")
    estimated = _dt("estimated_arrival") or _dt("estimated_in")
    if status in (FlightStatus.DELAYED, FlightStatus.EARLY) and estimated is None:
        # A delay claim with no estimate is not actionable — downgrade honestly.
        status = FlightStatus.UNKNOWN
    return FlightSnapshot(
        flight=flight.upper(), status=status,
        scheduled_arrival=scheduled, estimated_arrival=estimated,
        observed_at=observed_at, provenance=str(raw.get("provenance", "feed")),
        terminal=raw.get("terminal"),
    )


def adjust_pickup(*, booking_ref: str, snapshot: FlightSnapshot,
                  current_pickup_at: datetime, policy: TrackerPolicy,
                  now: datetime) -> "tuple[PickupAdjustment, Optional[Event]]":
    """Decide what (if anything) happens to a flight-linked pickup.

    Pure function: returns the adjustment decision + optional event. The
    caller applies the shift through the normal ride-state machinery (so
    LEGAL_TRANSITIONS and notifications fire as usual).
    """
    if snapshot.provenance == "none":
        return PickupAdjustment(
            booking_ref, snapshot.flight, "none", current_pickup_at, None,
            "no tracking data — pickup untouched", snapshot.provenance), None

    if snapshot.is_stale(now=now, max_age=policy.max_snapshot_age):
        return PickupAdjustment(
            booking_ref, snapshot.flight, "none", current_pickup_at, None,
            f"snapshot older than {policy.max_snapshot_age} — ignored",
            snapshot.provenance), None

    if snapshot.status in (FlightStatus.CANCELLED, FlightStatus.DIVERTED):
        ev = Event("flight.status_changed", "", now, {
            "flight": snapshot.flight, "status": snapshot.status.value,
            "booking": booking_ref, "provenance": snapshot.provenance,
        })
        return PickupAdjustment(
            booking_ref, snapshot.flight, "rebook_proposal",
            current_pickup_at, None,
            f"flight {snapshot.status.value} — dispatch must rebook; "
            "ride NOT auto-cancelled", snapshot.provenance), ev

    if snapshot.status == FlightStatus.LANDED:
        ev = Event("flight.status_changed", "", now, {
            "flight": snapshot.flight, "status": "landed",
            "booking": booking_ref, "provenance": snapshot.provenance,
        })
        return PickupAdjustment(
            booking_ref, snapshot.flight, "alert", current_pickup_at, None,
            "flight landed — dispatch board alert", snapshot.provenance), ev

    if snapshot.status not in (FlightStatus.DELAYED, FlightStatus.EARLY):
        return PickupAdjustment(
            booking_ref, snapshot.flight, "none", current_pickup_at, None,
            f"status {snapshot.status.value} needs no pickup change",
            snapshot.provenance), None

    # Delayed / early: candidate pickup = estimated arrival + curb buffer.
    assert snapshot.estimated_arrival is not None  # enforced at parse time
    candidate = snapshot.estimated_arrival + policy.curb_buffer
    delta = abs(candidate - current_pickup_at)
    if delta < policy.min_shift:
        return PickupAdjustment(
            booking_ref, snapshot.flight, "none", current_pickup_at, None,
            f"shift {delta} below hysteresis {policy.min_shift} — ignored",
            snapshot.provenance), None

    if (current_pickup_at - now) <= policy.lock_window:
        ev = Event("flight.status_changed", "", now, {
            "flight": snapshot.flight, "status": snapshot.status.value,
            "booking": booking_ref, "provenance": snapshot.provenance,
            "locked": True,
        })
        return PickupAdjustment(
            booking_ref, snapshot.flight, "alert", current_pickup_at, None,
            f"inside {policy.lock_window} lock window — human decides; "
            f"proposed {candidate.isoformat()}", snapshot.provenance), ev

    ev = Event("trip.rebooked", "", now, {
        "booking": booking_ref, "flight": snapshot.flight,
        "old_pickup_at": current_pickup_at.isoformat(),
        "new_pickup_at": candidate.isoformat(),
        "reason": f"flight {snapshot.status.value}",
        "provenance": snapshot.provenance,
    })
    return PickupAdjustment(
        booking_ref, snapshot.flight, "shift", current_pickup_at, candidate,
        f"flight {snapshot.status.value}: pickup shifted to estimated "
        "arrival + curb buffer", snapshot.provenance), ev
