"""Position ingest tests — Traccar webhook parse, store, Centrifugo publish,
fallback poller (report Ch. 5 flow 2)."""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "livery_core"))

from livery_core.adapters.position_ingest import (  # noqa: E402
    CentrifugoPublisher, IngestError, PositionFix, PositionStore,
    handle_webhook, parse_traccar_positions_response, parse_traccar_webhook,
    poll_once,
)

PAYLOAD = {
    "position": {
        "id": 1, "deviceId": 42,
        "latitude": 38.7223, "longitude": -9.1393,
        "fixTime": "2026-11-21T12:00:00Z",
        "speed": 25.0, "course": 90.0,
        "attributes": {"driverUniqueId": "drv-7"},
    },
    "device": {"id": 42, "uniqueId": "veh-lis-01"},
}


def test_parse_webhook_full():
    fix = parse_traccar_webhook(PAYLOAD)
    assert fix.device_id == "42" and fix.driver_id == "drv-7"
    assert fix.lat == 38.7223 and fix.lng == -9.1393
    assert fix.speed_kmh == 46.3            # 25 knots * 1.852
    assert fix.recorded_at.tzinfo is not None
    assert fix.source == "traccar_webhook"


def test_parse_rejects_garbage():
    with pytest.raises(IngestError):
        parse_traccar_webhook({"position": {"latitude": 95, "longitude": 0, "deviceId": 1,
                                            "fixTime": "2026-11-21T12:00:00Z"}})
    with pytest.raises(IngestError):
        parse_traccar_webhook({"device": {}})                       # no position
    with pytest.raises(IngestError):
        parse_traccar_webhook({"position": {"latitude": 1, "longitude": 1,
                                            "fixTime": "not-a-date", "deviceId": 1}})


def _fix(device="d", minutes=0):
    return PositionFix(device, 38.72, -9.14,
                       datetime(2026, 11, 21, 12, minutes, tzinfo=timezone.utc))


def test_store_drops_stale_and_keeps_history():
    store = PositionStore()
    assert store.put(_fix(minutes=5)) is True
    assert store.put(_fix(minutes=3)) is False   # out-of-order → dropped
    assert store.put(_fix(minutes=7)) is True
    assert store.latest("d").recorded_at.minute == 7
    assert len(store.history("d")) == 2          # only accepted fixes


def test_publish_shape_and_tenant_channel():
    calls = []

    def fake_transport(url, headers, body):
        calls.append((url, headers, body))
        return {"result": "ok"}

    pub = CentrifugoPublisher("http://centrifugo:8000", "secret", "acme",
                              transport=fake_transport)
    pub.publish_position(_fix(), ride_id="ride-1")
    url, headers, body = calls[0]
    assert url == "http://centrifugo:8000/api/publish"
    assert headers["Authorization"] == "apikey secret"
    assert body["channel"] == "tenant:acme#positions"
    assert body["data"]["source"] == "traccar_webhook"
    assert body["data"]["ride_id"] == "ride-1"


def test_handle_webhook_publishes_only_stored_fixes():
    calls = []
    pub = CentrifugoPublisher("http://c:8000", "k", "t",
                              transport=lambda u, h, b: calls.append(b) or {})
    store = PositionStore()
    fix1, stored1 = handle_webhook(PAYLOAD, store, pub)
    assert stored1 and len(calls) == 1
    # replay the SAME webhook (duplicate delivery) → dropped, not republished
    fix2, stored2 = handle_webhook(PAYLOAD, store, pub)
    assert not stored2 and len(calls) == 1


def test_poll_fallback_tolerates_bad_rows_and_labels_source():
    rows = [
        {"deviceId": 1, "latitude": 6.45, "longitude": 3.39,
         "fixTime": "2026-11-21T12:10:00Z", "speed": 10, "course": 0},
        {"garbage": True},  # must not kill the sweep
    ]
    fixes = parse_traccar_positions_response(rows)
    assert len(fixes) == 1 and fixes[0].source == "traccar_poll"

    store = PositionStore()
    result = poll_once(lambda: rows, store)
    assert result == {"fetched": 1, "stored": 1, "dropped_stale": 0}
    # re-poll same data → all stale, nothing stored
    result2 = poll_once(lambda: rows, store)
    assert result2["stored"] == 0 and result2["dropped_stale"] == 1
