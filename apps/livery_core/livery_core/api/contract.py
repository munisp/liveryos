"""API contract — the request/response shapes the passenger PWA and driver
PWA speak (report Ch. 5 §5.3.1). Framework-free handlers: the Frappe REST
layer (Phase 3 bench step) calls these and serializes the dicts; both apps
code against THIS contract, so the transport can change without app changes.

Rules the apps rely on:
- Every ETA carries eta_source ("estimate" | "valhalla") — UIs must render
  the label, never a bare number.
- Status transitions validate against the ride state machine HERE; the
  apps may gray out buttons, but the server is the enforcer.
- Position reports are validated (range, timestamp) before ingest; the
  response tells the app whether the fix was stored or dropped-stale so
  the offline queue logic stays simple.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol

from ..dispatch.board import DriverState, estimate_eta_minutes, haversine_km
from ..dispatch.doctype.livery_ride.livery_ride import LEGAL_TRANSITIONS


def ok(data: dict) -> dict:
    return {"ok": True, "data": data}


def err(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


@dataclass(frozen=True)
class RideView:
    """What the passenger sees for one booking."""
    ride_id: str
    status: str
    service_class: str
    pickup_at: datetime
    pickup_address: str
    dropoff_address: str
    quoted_minor: Optional[int]
    currency: str
    pickup_lat: Optional[float] = None
    pickup_lng: Optional[float] = None
    driver_name: Optional[str] = None
    vehicle_label: Optional[str] = None
    eta_minutes: Optional[int] = None
    eta_source: str = "none"               # estimate | valhalla | none


class RideRepo(Protocol):
    """Persistence boundary — bench implementation reads DocTypes."""
    def get_ride(self, tenant: str, ride_id: str) -> Optional[RideView]: ...
    def get_status(self, tenant: str, ride_id: str) -> Optional[str]: ...
    def set_status(self, tenant: str, ride_id: str, status: str) -> None: ...


# --------------------------------------------------------------------------
# Passenger surface
# --------------------------------------------------------------------------

def passenger_booking_view(repo: RideRepo, tenant: str, ride_id: str) -> dict:
    ride = repo.get_ride(tenant, ride_id)
    if ride is None:
        return err("not_found", f"no booking {ride_id}")
    return ok({
        "ride_id": ride.ride_id,
        "status": ride.status,
        "service_class": ride.service_class,
        "pickup_at": ride.pickup_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "pickup_address": ride.pickup_address,
        "dropoff_address": ride.dropoff_address,
        "quoted_minor": ride.quoted_minor,
        "currency": ride.currency,
        "driver_name": ride.driver_name,
        "vehicle_label": ride.vehicle_label,
        "eta_minutes": ride.eta_minutes,
        "eta_source": ride.eta_source,      # UI renders the label — contract rule
    })


def passenger_eta(ride: RideView, driver: Optional[DriverState]) -> dict:
    """Live ETA for the tracking screen. Straight-line estimate with the
    source label; 'none' when inputs are missing — never a bare guess."""
    if ride.status in ("COMPLETED", "INVOICED", "CANCELLED", "NO_SHOW"):
        return ok({"eta_minutes": None, "eta_source": "none",
                   "reason": f"ride is {ride.status}"})
    if ride.pickup_lat is None or ride.pickup_lng is None:
        return ok({"eta_minutes": None, "eta_source": "none",
                   "reason": "pickup coordinates not geocoded yet"})
    if driver is None or driver.last_lat is None:
        return ok({"eta_minutes": None, "eta_source": "none",
                   "reason": "no driver position yet"})
    dist = haversine_km(driver.last_lat, driver.last_lng,
                        ride.pickup_lat, ride.pickup_lng)
    return ok({"eta_minutes": estimate_eta_minutes(dist),
               "eta_source": "estimate",
               "distance_km": round(dist, 2)})


# --------------------------------------------------------------------------
# Driver surface
# --------------------------------------------------------------------------

def driver_transition(repo: RideRepo, tenant: str, ride_id: str,
                      driver_id: str, target: str) -> dict:
    """Driver app status action (accept→ASSIGNED, en route, at pickup,
    start, complete). Server-side state-machine enforcement."""
    current = repo.get_status(tenant, ride_id)
    if current is None:
        return err("not_found", f"no ride {ride_id}")
    if target not in LEGAL_TRANSITIONS.get(current, set()):
        return err("illegal_transition",
                   f"{current} → {target} is not a legal transition")
    repo.set_status(tenant, ride_id, target)
    return ok({"ride_id": ride_id, "from": current, "to": target,
               "changed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")})


def driver_position_report(tenant: str, driver_id: str, device_id: str,
                           lat: float, lng: float, recorded_at: datetime,
                           speed_kmh: float = 0.0, heading: float = 0.0) -> dict:
    """Validate a driver-app GPS report before it enters position ingest.
    Rejects impossible coordinates and future timestamps (device clock
    skew beyond 5 min forward is dropped, not clamped — clamping would
    fabricate a position the device never measured). Naive timestamps are
    REJECTED, not coerced: a naive device clock is local time, and assuming
    UTC could shift a fix by hours (Lagos is UTC+1, devices roam). The apps
    must send ISO-8601 with an explicit offset."""
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return err("bad_coordinates", f"out of range: {lat},{lng}")
    if recorded_at.tzinfo is None:
        return err("naive_timestamp",
                   "recorded_at must carry a timezone offset (ISO-8601) — "
                   "coercing local device time to UTC would fabricate the fix time")
    now = datetime.now(timezone.utc)
    if (recorded_at - now).total_seconds() > 300:
        return err("future_timestamp",
                   f"recorded_at {recorded_at.isoformat()} is >5 min ahead of server")
    return ok({
        "tenant": tenant, "driver_id": driver_id, "device_id": device_id,
        "lat": lat, "lng": lng, "speed_kmh": speed_kmh, "heading": heading,
        "recorded_at": recorded_at.isoformat().replace("+00:00", "Z"),
        "accepted": True,
    })
