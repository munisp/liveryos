"""Dispatch board — assignment engine (report Ch. 3 §3.2.2).

Framework-free server logic behind the dispatch console. Given a ride and the
current fleet snapshot, produce ranked driver suggestions or an automatic
assignment. Hard filters first (vehicle class, compliance validity, idle),
then proximity ranking by haversine distance from the driver's last known
position to the pickup point.

The Beveren-style board cycle this supports:
    QUOTED --(assign)--> ASSIGNED --(driver en route)--> EN_ROUTE --> ...
    ASSIGNED --(requeue)--> QUOTED     # driver fell through; back to the board

Honesty rule (platform-wide): ETA here is a straight-line estimate, NOT a
routed ETA. It is labeled `eta_source="estimate"`. Valhalla-routed ETAs
(`eta_source="valhalla"`) land when the bench integration calls the routing
service; callers must surface the source to the UI so nothing fake-looking
is ever presented as a live routed ETA.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from math import asin, cos, radians, sin, sqrt
from typing import Optional


EARTH_RADIUS_KM = 6371.0088
DEFAULT_CITY_SPEED_KMH = 28.0  # conservative urban average; Lisbon/Lagos surface streets


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km."""
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(a))


def estimate_eta_minutes(distance_km: float, avg_speed_kmh: float = DEFAULT_CITY_SPEED_KMH) -> int:
    """Straight-line ETA estimate in whole minutes (ceil-ish, min 1).

    This is deliberately conservative and always labeled an estimate —
    see module docstring honesty rule.
    """
    if distance_km <= 0:
        return 1
    minutes = distance_km / avg_speed_kmh * 60
    return max(1, int(minutes + 0.999))  # ceil without importing math.ceil twice


@dataclass(frozen=True)
class DriverState:
    """Fleet snapshot row for one driver at board-tick time."""
    driver_id: str
    vehicle_class: str                    # must match ride service_class
    compliance_valid: bool                # licence/medical in date (gap item 12 feed)
    idle: bool                            # no active ride (ASSIGNED..IN_PROGRESS)
    last_lat: Optional[float] = None      # None = no position fix yet
    last_lng: Optional[float] = None
    last_fix_at: Optional[datetime] = None


@dataclass(frozen=True)
class BoardRide:
    """The ride the board is trying to place."""
    ride_id: str
    service_class: str
    pickup_lat: float
    pickup_lng: float
    pickup_at: datetime


@dataclass(frozen=True)
class Suggestion:
    driver_id: str
    distance_km: Optional[float]          # None when the driver has no fix
    eta_minutes: Optional[int]
    eta_source: str                       # "estimate" | "none" (never "routed" here)
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class AssignmentResult:
    assigned: bool
    driver_id: Optional[str] = None
    reason: str = ""


def _filter_drivers(ride: BoardRide, drivers: list[DriverState]) -> tuple[list[DriverState], dict[str, str]]:
    """Hard filters. Returns (eligible, rejection_reasons_by_driver)."""
    eligible: list[DriverState] = []
    rejected: dict[str, str] = {}
    for d in drivers:
        if d.vehicle_class != ride.service_class:
            rejected[d.driver_id] = f"vehicle class {d.vehicle_class} != {ride.service_class}"
        elif not d.compliance_valid:
            rejected[d.driver_id] = "compliance expired (licence/medical)"
        elif not d.idle:
            rejected[d.driver_id] = "on an active ride"
        else:
            eligible.append(d)
    return eligible, rejected


def suggest_assignments(ride: BoardRide, drivers: list[DriverState],
                        top_n: int = 5) -> list[Suggestion]:
    """Rank eligible drivers: nearest first; drivers with no position fix go
    last (distance None) ordered by driver_id for determinism."""
    eligible, _ = _filter_drivers(ride, drivers)

    def key(d: DriverState):
        if d.last_lat is None:
            return (1, 0.0, d.driver_id)      # no fix → bottom, deterministic
        dist = haversine_km(d.last_lat, d.last_lng, ride.pickup_lat, ride.pickup_lng)
        return (0, dist, d.driver_id)

    out: list[Suggestion] = []
    for d in sorted(eligible, key=key)[:top_n]:
        if d.last_lat is None:
            out.append(Suggestion(d.driver_id, None, None, "none",
                                  ("no position fix yet — dispatch by local knowledge",)))
        else:
            dist = haversine_km(d.last_lat, d.last_lng, ride.pickup_lat, ride.pickup_lng)
            out.append(Suggestion(
                d.driver_id, round(dist, 2), estimate_eta_minutes(dist), "estimate",
                (f"{dist:.1f} km straight-line from pickup",)))
    return out


def auto_assign(ride: BoardRide, drivers: list[DriverState],
                max_distance_km: float = 15.0) -> AssignmentResult:
    """Auto-assign the nearest eligible driver WITH a position fix inside
    max_distance_km. Never assigns a driver whose position is unknown —
    an unexplained assignment is worse than a board suggestion."""
    eligible, rejected = _filter_drivers(ride, drivers)
    if not eligible:
        why = "; ".join(f"{k}: {v}" for k, v in sorted(rejected.items())) or "fleet empty"
        return AssignmentResult(False, None, f"no eligible driver — {why}")
    with_fix = [d for d in eligible if d.last_lat is not None]
    if not with_fix:
        return AssignmentResult(False, None,
                                "eligible drivers have no position fix — manual dispatch required")
    nearest = min(with_fix, key=lambda d: haversine_km(d.last_lat, d.last_lng,
                                                       ride.pickup_lat, ride.pickup_lng))
    dist = haversine_km(nearest.last_lat, nearest.last_lng, ride.pickup_lat, ride.pickup_lng)
    if dist > max_distance_km:
        return AssignmentResult(False, None,
                                f"nearest driver {nearest.driver_id} is {dist:.1f} km away "
                                f"(> {max_distance_km} km cap)")
    return AssignmentResult(True, nearest.driver_id,
                            f"nearest eligible, {dist:.1f} km straight-line "
                            f"(~{estimate_eta_minutes(dist)} min estimate)")


@dataclass
class Board:
    """Minimal board state: which rides are unplaced and the latest fleet
    snapshot. Persistence lives in Frappe DocTypes; this is the decision
    layer the console API calls."""
    unplaced: list[BoardRide] = field(default_factory=list)
    fleet: dict[str, DriverState] = field(default_factory=dict)

    def tick(self, auto_max_distance_km: float = 15.0) -> dict[str, AssignmentResult]:
        """One board cycle: try auto-assign for every unplaced ride. Rides
        that fail stay on the board with a reason for the console to show."""
        results: dict[str, AssignmentResult] = {}
        for ride in list(self.unplaced):
            res = auto_assign(ride, list(self.fleet.values()), auto_max_distance_km)
            results[ride.ride_id] = res
            if res.assigned:
                self.unplaced.remove(ride)
                # driver is now committed — next tick sees them non-idle
                d = self.fleet[res.driver_id]
                self.fleet[res.driver_id] = DriverState(
                    d.driver_id, d.vehicle_class, d.compliance_valid, False,
                    d.last_lat, d.last_lng, d.last_fix_at)
        return results
