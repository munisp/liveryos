"""GRES mapping tests — round-trip conformance primitive (draft mapping)."""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "livery_core"))

from livery_core.affiliates.gres import (  # noqa: E402
    FROM_GRES_STATUS, TO_GRES_STATUS, GresBooking, GresMappingError,
    from_gres_payload, from_gres_status, roundtrip_check, to_gres_payload,
    to_gres_status,
)

BOOKING = GresBooking(
    reservation_id="RES-9001", status="DISPATCHED",
    passenger_name="Adaeze Okafor",
    pickup_at=datetime(2026, 12, 1, 8, 30, tzinfo=timezone.utc),
    pickup_address="Murtala Muhammed Intl Airport, Lagos",
    dropoff_address="Eko Hotel, Victoria Island",
    vehicle_class="suv", rate_minor=5_000_000, currency="NGN",
    originator_system="liveryos-tenant-lagos1", affiliate_system="partner-ikoyi",
)


def test_status_maps_both_ways_no_collision():
    for livery, gres in TO_GRES_STATUS.items():
        assert from_gres_status(gres) in FROM_GRES_STATUS.values()
    assert to_gres_status("EN_ROUTE") == "ENROUTE"
    assert from_gres_status("Arrived") == "AT_PICKUP"
    assert from_gres_status("Canceled") == "CANCELLED"   # US spelling tolerated
    with pytest.raises(GresMappingError):
        to_gres_status("BOGUS")
    with pytest.raises(GresMappingError):
        from_gres_status("FLYING")


def test_roundtrip_is_identity():
    assert roundtrip_check(BOOKING)
    # and with null rate (per-booking quote case)
    b2 = GresBooking(**{**BOOKING.__dict__, "rate_minor": None})
    assert roundtrip_check(b2)


def test_payload_wire_shape():
    p = to_gres_payload(BOOKING)
    assert p["reservationId"] == "RES-9001"
    assert p["pickupDateTime"].endswith("Z")
    assert p["rateMinorUnits"] == 5_000_000 and p["rateCurrency"] == "NGN"
    assert p["affiliateSystemId"] == "partner-ikoyi"


def test_from_payload_validates_at_boundary():
    p = to_gres_payload(BOOKING)
    p["status"] = "TELEPORTING"
    with pytest.raises(GresMappingError):
        from_gres_payload(p)
    p2 = to_gres_payload(BOOKING)
    del p2["passengerName"]
    with pytest.raises(GresMappingError):
        from_gres_payload(p2)
    p3 = to_gres_payload(BOOKING)
    p3["pickupDateTime"] = "soon-ish"
    with pytest.raises(GresMappingError):
        from_gres_payload(p3)
