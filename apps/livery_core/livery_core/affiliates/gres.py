"""GRES mapping layer (draft) — GNet conformance prep (docs/phase0/gnet-application-prep.md).

STATUS: DRAFT mapping, honestly labeled. The GRES standard's exact field
names are finalized during GNet sandbox conformance (budgeted 4–8 weeks,
Phase 3 risk register). What this module guarantees TODAY is the property
conformance actually tests: a canonical booking survives canonical → GRES
→ canonical LOSSLESSLY, and status codes map both directions with no
collisions. When GNet's schema lands, only the field names in _FIELD_MAP
change — the round-trip property and its tests stay.

GRES status codes (per GNet/NLA public descriptions) are string codes on
the reservation; we keep our Livery Ride statuses internal and translate
at this boundary only — decommissioning or re-mapping the network layer
never touches dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

# Livery Ride status -> GRES-ish status code (draft labels).
TO_GRES_STATUS = {
    "QUOTED": "BOOKED",
    "ASSIGNED": "DISPATCHED",
    "EN_ROUTE": "ENROUTE",
    "AT_PICKUP": "ARRIVED",
    "IN_PROGRESS": "ONBOARD",
    "COMPLETED": "DONE",
    "INVOICED": "DONE",          # billing state, not a network state
    "CANCELLED": "CANCELLED",
    "NO_SHOW": "NOSHOW",
}
FROM_GRES_STATUS = {
    "BOOKED": "QUOTED",
    "CONFIRMED": "QUOTED",
    "DISPATCHED": "ASSIGNED",
    "ENROUTE": "EN_ROUTE",
    "ARRIVED": "AT_PICKUP",
    "ONBOARD": "IN_PROGRESS",
    "DONE": "COMPLETED",
    "COMPLETED": "COMPLETED",
    "CANCELLED": "CANCELLED",
    "CANCELED": "CANCELLED",
    "NOSHOW": "NO_SHOW",
    "NO_SHOW": "NO_SHOW",
}


class GresMappingError(ValueError):
    pass


def to_gres_status(livery_status: str) -> str:
    try:
        return TO_GRES_STATUS[livery_status]
    except KeyError:
        raise GresMappingError(f"no GRES code for livery status {livery_status!r}")


def from_gres_status(gres_status: str) -> str:
    key = (gres_status or "").strip().upper()
    if key not in FROM_GRES_STATUS:
        raise GresMappingError(f"no livery status for GRES code {gres_status!r}")
    return FROM_GRES_STATUS[key]


@dataclass(frozen=True)
class GresBooking:
    """Our side of a GRES reservation exchange — field names here are the
    DRAFT mapping surface; swap to the sandbox schema in one place."""
    reservation_id: str
    status: str                    # GRES code
    passenger_name: str
    pickup_at: datetime
    pickup_address: str
    dropoff_address: str
    vehicle_class: str
    rate_minor: Optional[int]
    currency: str
    originator_system: str         # who farmed it out
    affiliate_system: str          # who runs it


# The single point where canonical field names meet the wire names.
_FIELD_MAP = {
    "reservation_id": "reservationId",
    "status": "status",
    "passenger_name": "passengerName",
    "pickup_at": "pickupDateTime",
    "pickup_address": "pickupAddress",
    "dropoff_address": "dropoffAddress",
    "vehicle_class": "vehicleClass",
    "rate_minor": "rateMinorUnits",
    "currency": "rateCurrency",
    "originator_system": "originatorSystemId",
    "affiliate_system": "affiliateSystemId",
}


def to_gres_payload(b: GresBooking) -> dict:
    return {
        _FIELD_MAP["reservation_id"]: b.reservation_id,
        _FIELD_MAP["status"]: b.status,
        _FIELD_MAP["passenger_name"]: b.passenger_name,
        _FIELD_MAP["pickup_at"]: b.pickup_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        _FIELD_MAP["pickup_address"]: b.pickup_address,
        _FIELD_MAP["dropoff_address"]: b.dropoff_address,
        _FIELD_MAP["vehicle_class"]: b.vehicle_class,
        _FIELD_MAP["rate_minor"]: b.rate_minor,
        _FIELD_MAP["currency"]: b.currency,
        _FIELD_MAP["originator_system"]: b.originator_system,
        _FIELD_MAP["affiliate_system"]: b.affiliate_system,
    }


def from_gres_payload(p: dict) -> GresBooking:
    def need(wire: str):
        v = p.get(wire)
        if v is None or v == "":
            raise GresMappingError(f"missing required GRES field {wire!r}")
        return v
    raw_dt = need(_FIELD_MAP["pickup_at"])
    try:
        dt = datetime.fromisoformat(str(raw_dt).replace("Z", "+00:00"))
    except ValueError as e:
        raise GresMappingError(f"bad GRES pickupDateTime {raw_dt!r}: {e}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    status = need(_FIELD_MAP["status"])
    from_gres_status(status)  # validate at the boundary, not downstream
    return GresBooking(
        reservation_id=str(need(_FIELD_MAP["reservation_id"])),
        status=str(status).upper(),
        passenger_name=str(need(_FIELD_MAP["passenger_name"])),
        pickup_at=dt,
        pickup_address=str(need(_FIELD_MAP["pickup_address"])),
        dropoff_address=str(need(_FIELD_MAP["dropoff_address"])),
        vehicle_class=str(need(_FIELD_MAP["vehicle_class"])),
        rate_minor=p.get(_FIELD_MAP["rate_minor"]),
        currency=str(need(_FIELD_MAP["currency"])).upper(),
        originator_system=str(need(_FIELD_MAP["originator_system"])),
        affiliate_system=str(need(_FIELD_MAP["affiliate_system"])),
    )


def roundtrip_check(b: GresBooking) -> bool:
    """Conformance self-test primitive: canonical → wire → canonical must
    be an identity on every field. GNet conformance ping-pong then becomes
    'make roundtrip_check pass against THEIR samples', not a rewrite."""
    back = from_gres_payload(to_gres_payload(b))
    return back == b
