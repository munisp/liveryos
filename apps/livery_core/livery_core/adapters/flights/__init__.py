"""Flight tracking (GA parity gap (b)).

GA's flight-aware dispatch auto-adjusts airport pickups when flights delay
or cancel. LiveryOS does the same with an injectable AeroAPI-class feed,
under strict honesty rules:

- Every status carries provenance: "feed" (provider said so), "manual"
  (dispatcher typed it), "none" (unknown — we never guess).
- Pickup adjustment has hysteresis: small wobbles (< threshold, default
  10 min) do NOT move a booked pickup; re-booking churn is worse than a
  slightly early driver.
- A pickup inside the lock window (default 45 min before pickup) is never
  auto-moved — at that point a human owns the decision; the tracker emits
  an alert instead.
- Cancelled flights never silently cancel the ride: they emit
  flight.status_changed + a rebook proposal for dispatch.
- Stale feed data (older than max_age) is ignored and counted — acting on
  stale positions is how drivers get sent to the wrong terminal.
"""

from .tracker import (
    FlightStatus,
    FlightSnapshot,
    PickupAdjustment,
    TrackerPolicy,
    adjust_pickup,
    parse_feed_snapshot,
)

__all__ = [
    "FlightStatus", "FlightSnapshot", "PickupAdjustment", "TrackerPolicy",
    "adjust_pickup", "parse_feed_snapshot",
]
