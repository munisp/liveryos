"""API contract tests — shared passenger/driver handler shapes."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "livery_core"))

from livery_core.api.contract import (  # noqa: E402
    RideView, driver_position_report, driver_transition, passenger_booking_view,
    passenger_eta,
)
from livery_core.dispatch.board import DriverState  # noqa: E402

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
RIDE = RideView("ride-1", "ASSIGNED", "sedan", NOW,
                "Aeroporto de Lisboa", "Rua Augusta 24",
                14550, "EUR", pickup_lat=38.77, pickup_lng=-9.13,
                driver_name="Carlos", vehicle_label="Mercedes E · 12-AB-34")


class FakeRepo:
    def __init__(self, status="ASSIGNED", ride=RIDE):
        self.status = status
        self.ride = ride
        self.set_calls = []

    def get_ride(self, tenant, ride_id):
        return self.ride if ride_id == self.ride.ride_id else None

    def get_status(self, tenant, ride_id):
        return self.status if ride_id == self.ride.ride_id else None

    def set_status(self, tenant, ride_id, status):
        self.set_calls.append(status)
        self.status = status


def test_passenger_view_shape_and_404():
    res = passenger_booking_view(FakeRepo(), "acme", "ride-1")
    assert res["ok"] and res["data"]["status"] == "ASSIGNED"
    assert res["data"]["eta_source"] == "none"        # label always present
    assert res["data"]["pickup_at"].endswith("Z")
    res404 = passenger_booking_view(FakeRepo(), "acme", "nope")
    assert not res404["ok"] and res404["error"]["code"] == "not_found"


def test_passenger_eta_labeled_estimate():
    driver = DriverState("d1", "sedan", True, False, 38.75, -9.14, NOW)
    res = passenger_eta(RIDE, driver)
    assert res["ok"] and res["data"]["eta_source"] == "estimate"
    assert res["data"]["eta_minutes"] >= 1


def test_passenger_eta_none_paths():
    no_fix = DriverState("d1", "sedan", True, False, None, None, None)
    res = passenger_eta(RIDE, no_fix)
    assert res["data"]["eta_source"] == "none"
    done = RideView(**{**RIDE.__dict__, "status": "COMPLETED"})
    res2 = passenger_eta(done, None)
    assert res2["data"]["eta_minutes"] is None and "COMPLETED" in res2["data"]["reason"]
    nogeocode = RideView(**{**RIDE.__dict__, "pickup_lat": None, "pickup_lng": None})
    assert passenger_eta(nogeocode, no_fix)["data"]["eta_source"] == "none"


def test_driver_transition_enforces_state_machine():
    repo = FakeRepo(status="ASSIGNED")
    res = driver_transition(repo, "acme", "ride-1", "d1", "EN_ROUTE")
    assert res["ok"] and res["data"]["from"] == "ASSIGNED"
    bad = driver_transition(repo, "acme", "ride-1", "d1", "INVOICED")
    assert not bad["ok"] and bad["error"]["code"] == "illegal_transition"
    repo2 = FakeRepo(status="ASSIGNED")
    requeue = driver_transition(repo2, "acme", "ride-1", "d1", "QUOTED")
    assert requeue["ok"]  # board requeue is legal from ASSIGNED


def test_driver_position_report_validation():
    now = datetime.now(timezone.utc)
    res = driver_position_report("acme", "d1", "dev-1", 38.72, -9.14, now)
    assert res["ok"] and res["data"]["accepted"]
    bad = driver_position_report("acme", "d1", "dev-1", 95.0, -9.14, NOW)
    assert not bad["ok"] and bad["error"]["code"] == "bad_coordinates"
    future = driver_position_report("acme", "d1", "dev-1", 38.72, -9.14,
                                    datetime.now(timezone.utc) + timedelta(minutes=10))
    assert not future["ok"] and future["error"]["code"] == "future_timestamp"
    naive = driver_position_report("acme", "d1", "dev-1", 38.72, -9.14,
                                   datetime.now().replace(tzinfo=None))
    assert not naive["ok"] and naive["error"]["code"] == "naive_timestamp"
    # explicit non-UTC offset (Lagos UTC+1) is accepted
    lagos = datetime.now(timezone(timedelta(hours=1)))
    assert driver_position_report("acme", "d1", "dev-1", 6.45, 3.39, lagos)["ok"]
