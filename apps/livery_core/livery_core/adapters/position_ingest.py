"""Position ingest — Traccar → position store → Centrifugo (report Ch. 5 flow 2).

Canonical realtime flow:
    Traccar webhook ──► parse_traccar_webhook ──► PositionStore (latest+history)
                          └──► CentrifugoPublisher ──► channel tenant:{slug}#positions
                                └──► dispatch boards + passenger tracking UIs

Webhooks are primary; the hourly `poll_traccar_fallback` (registered in
hooks.py) covers missed webhooks. Both paths produce the same PositionFix
shape and the same publish, so consumers cannot tell (and do not care)
which path delivered a fix — but `source` is recorded for provenance.

Framework-free: frappe is imported inside the scheduler wrapper only, so the
whole module is pytest-able without a bench. Transports are injectable.
"""

from __future__ import annotations

import json
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional


class IngestError(ValueError):
    """Malformed webhook payload or poll response."""


@dataclass(frozen=True)
class PositionFix:
    device_id: str
    lat: float
    lng: float
    recorded_at: datetime
    speed_kmh: float = 0.0
    heading: float = 0.0
    driver_id: Optional[str] = None
    source: str = "traccar_webhook"        # traccar_webhook | traccar_poll


def _parse_dt(value) -> datetime:
    """Traccar emits ISO-8601 with Z. Be strict: a bad timestamp means a bad fix."""
    if not isinstance(value, str) or not value:
        raise IngestError(f"missing/invalid timestamp: {value!r}")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise IngestError(f"unparseable timestamp {value!r}: {e}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_traccar_webhook(payload: dict) -> PositionFix:
    """Traccar webhook body: {"position": {...}, "device": {...}} (event
    wrapper tolerated). Maps to the canonical fix. Knots → km/h."""
    if not isinstance(payload, dict):
        raise IngestError("payload is not an object")
    pos = payload.get("position")
    dev = payload.get("device") or {}
    if not isinstance(pos, dict):
        raise IngestError("missing position object")
    try:
        lat = float(pos["latitude"])
        lng = float(pos["longitude"])
    except (KeyError, TypeError, ValueError) as e:
        raise IngestError(f"bad coordinates: {e}")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise IngestError(f"coordinates out of range: {lat},{lng}")
    device_id = str(dev.get("id") or pos.get("deviceId") or "")
    if not device_id:
        raise IngestError("no device id in payload")
    speed_knots = float(pos.get("speed") or 0.0)
    driver = (pos.get("attributes") or {}).get("driverUniqueId") or dev.get("uniqueId")
    return PositionFix(
        device_id=device_id,
        lat=lat, lng=lng,
        recorded_at=_parse_dt(pos.get("fixTime") or pos.get("deviceTime") or pos.get("serverTime")),
        speed_kmh=round(speed_knots * 1.852, 1),
        heading=float(pos.get("course") or 0.0),
        driver_id=str(driver) if driver else None,
        source="traccar_webhook",
    )


class PositionStore:
    """Latest fix per device + bounded history. Persistence on the bench is
    Redis/DocType-backed; this in-memory implementation is the reference
    behavior and the test double."""

    def __init__(self, history_per_device: int = 120):
        self._latest: dict[str, PositionFix] = {}
        self._history: dict[str, deque[PositionFix]] = {}
        self._cap = history_per_device

    def put(self, fix: PositionFix) -> bool:
        """Store. Returns False (dropped) if the fix is older than the latest
        we hold for the device — out-of-order delivery is normal on fallback
        polls overlapping webhooks."""
        latest = self._latest.get(fix.device_id)
        if latest and fix.recorded_at <= latest.recorded_at:
            return False
        self._latest[fix.device_id] = fix
        self._history.setdefault(fix.device_id, deque(maxlen=self._cap)).append(fix)
        return True

    def latest(self, device_id: str) -> Optional[PositionFix]:
        return self._latest.get(device_id)

    def all_latest(self) -> list[PositionFix]:
        return list(self._latest.values())

    def history(self, device_id: str) -> list[PositionFix]:
        return list(self._history.get(device_id, ()))


Transport = Callable[[str, dict, dict], dict]  # (url, headers, json_body) -> response dict


def _urllib_transport(url: str, headers: dict, body: dict) -> dict:
    req = urllib.request.Request(
        url, method="POST",
        headers={"Content-Type": "application/json", **headers},
        data=json.dumps(body).encode())
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"{}")


@dataclass
class CentrifugoPublisher:
    """Publishes position updates to a tenant-namespaced Centrifugo channel
    via the server HTTP API. Channel: tenant:{tenant}#positions — matches
    infra/centrifugo.json namespace config."""
    base_url: str                          # e.g. http://centrifugo:8000
    api_key: str
    tenant: str
    transport: Transport = _urllib_transport

    def publish_position(self, fix: PositionFix, ride_id: Optional[str] = None) -> dict:
        body = {
            "channel": f"tenant:{self.tenant}#positions",
            "data": {
                "device_id": fix.device_id,
                "driver_id": fix.driver_id,
                "lat": fix.lat, "lng": fix.lng,
                "speed_kmh": fix.speed_kmh, "heading": fix.heading,
                "recorded_at": fix.recorded_at.isoformat(),
                "source": fix.source,
                "ride_id": ride_id,
            },
        }
        return self.transport(
            f"{self.base_url}/api/publish",
            {"Authorization": f"apikey {self.api_key}"}, body)


def handle_webhook(payload: dict, store: PositionStore,
                   publisher: Optional[CentrifugoPublisher] = None,
                   ride_id: Optional[str] = None) -> tuple[PositionFix, bool]:
    """Full webhook path: parse → store (drop stale) → publish. Returns
    (fix, stored). Publish happens only for fixes that were stored — boards
    never see out-of-order regressions."""
    fix = parse_traccar_webhook(payload)
    stored = store.put(fix)
    if stored and publisher:
        publisher.publish_position(fix, ride_id=ride_id)
    return fix, stored


# ---------------------------------------------------------------------------
# Fallback poller (hooks.py: hourly livery_core.adapters.position_ingest.
# poll_traccar_fallback). Webhooks are primary; this sweeps up misses.
# ---------------------------------------------------------------------------

def parse_traccar_positions_response(rows: list[dict]) -> list[PositionFix]:
    """Traccar GET /api/positions returns a list of position objects (no
    device wrapper). Same canonical shape, source=traccar_poll."""
    fixes: list[PositionFix] = []
    for pos in rows:
        try:
            fixes.append(parse_traccar_webhook({"position": pos, "device": {}}))
        except IngestError:
            continue  # one bad row must not kill the sweep
    return [
        PositionFix(f.device_id, f.lat, f.lng, f.recorded_at, f.speed_kmh,
                    f.heading, f.driver_id, "traccar_poll")
        for f in fixes
    ]


def poll_once(fetch: Callable[[], list[dict]], store: PositionStore,
              publisher: Optional[CentrifugoPublisher] = None) -> dict:
    """One fallback sweep. `fetch` does the authenticated Traccar call
    (session-cookie auth lives on the bench side). Returns counts."""
    fixes = parse_traccar_positions_response(fetch())
    stored = 0
    for fix in fixes:
        if store.put(fix):
            stored += 1
            if publisher:
                publisher.publish_position(fix)
    return {"fetched": len(fixes), "stored": stored,
            "dropped_stale": len(fixes) - stored}


def poll_traccar_fallback():  # pragma: no cover — bench-only wiring
    """hooks.py hourly target. Thin: reads site config, builds an
    authenticated fetch against LIVERY_TRACCAR_URL, runs one sweep per
    tenant site. Kept import-light at module level."""
    import frappe  # noqa: PLC0415 — bench only
    import requests  # noqa: PLC0415 — bench dependency

    conf = frappe.get_conf()
    base = conf.get("livery_traccar_url") or "http://traccar:8082"
    user = conf.get("livery_traccar_user")
    password = conf.get("livery_traccar_password")
    token = conf.get("livery_centrifugo_api_key")
    cent_url = conf.get("livery_centrifugo_url") or "http://centrifugo:8000"
    tenant = frappe.local.site.split(".")[0]

    session = requests.Session()
    session.auth = (user, password)

    def fetch() -> list[dict]:
        resp = session.get(f"{base}/api/positions", timeout=15)
        resp.raise_for_status()
        return resp.json()

    store = _bench_store()
    publisher = CentrifugoPublisher(cent_url, token, tenant) if token else None
    result = poll_once(fetch, store, publisher)
    frappe.logger("livery.position_ingest").info(f"traccar fallback sweep: {result}")


def _bench_store():  # pragma: no cover — bench only
    """Redis-backed store matching PositionStore semantics. Until the Redis
    layer lands (Phase 2 bench integration), the in-memory reference store
    is used per-sweep — honest limitation: dedupe across sweeps is lost,
    stale-drop still works within a sweep."""
    return PositionStore()
