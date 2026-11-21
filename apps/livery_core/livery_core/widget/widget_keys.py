"""Embeddable-widget keys: issuance, origin checks, rate limiting.

The booking widget runs on OTHER people's websites. The key identifies the
tenant; the origin allowlist stops key reuse on random sites; the token
bucket stops scripted abuse. Keys carry no passenger identity — they mint
bookings that the tenant's normal pipeline validates.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple


class WidgetOriginError(PermissionError):
    """Request origin not on the key's allowlist."""


class RateLimitExceeded(RuntimeError):
    """Token bucket empty — client must back off."""


@dataclass(frozen=True)
class EmbedKey:
    tenant: str
    key_id: str
    key_hash: str                     # sha256 — the raw key is never stored
    allowed_origins: Tuple[str, ...]  # e.g. ("https://hotel.example.pt",)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    revoked_at: Optional[datetime] = None
    rate_per_minute: int = 30

    def active(self, now: Optional[datetime] = None) -> bool:
        return self.revoked_at is None


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def issue_key(keystore: "KeyStore", *, tenant: str,
              allowed_origins: Tuple[str, ...],
              rate_per_minute: int = 30) -> "tuple[EmbedKey, str]":
    """Returns (stored_key, raw_key). The raw key is shown ONCE to the
    operator; only its hash persists."""
    if not allowed_origins:
        raise ValueError("widget key requires at least one allowed origin — "
                         "an any-origin key is an open booking faucet")
    for o in allowed_origins:
        if not o.startswith("https://") and not o.startswith("http://localhost"):
            raise ValueError(f"origin {o!r} must be https (localhost exempt for dev)")
    raw = f"lvk_{secrets.token_urlsafe(32)}"
    key = EmbedKey(tenant=tenant, key_id=secrets.token_hex(8),
                   key_hash=hash_key(raw), allowed_origins=allowed_origins,
                   rate_per_minute=rate_per_minute)
    keystore.save(key)
    return key, raw


class _Bucket:
    __slots__ = ("tokens", "ts")

    def __init__(self, capacity: int, ts: datetime):
        self.tokens = float(capacity)
        self.ts = ts


class KeyStore:
    """In-memory store + buckets. Postgres adapter owns persistence; bucket
    state is per-process by design (bursty abuse protection, not billing)."""

    def __init__(self):
        self._by_hash: Dict[str, EmbedKey] = {}
        self._buckets: Dict[str, _Bucket] = {}

    def save(self, key: EmbedKey) -> None:
        self._by_hash[key.key_hash] = key
        # bucket is created lazily on first consume (with that call's clock) —
        # a bucket born on wall-clock time would drain/refill wrongly for
        # callers passing a different (e.g. replayed/test) clock.

    def get(self, raw_key: str) -> Optional[EmbedKey]:
        return self._by_hash.get(hash_key(raw_key))

    def revoke(self, key: EmbedKey, *, now: Optional[datetime] = None) -> EmbedKey:
        revoked = EmbedKey(**{**key.__dict__,
                              "revoked_at": now or datetime.now(timezone.utc)})
        self._by_hash[key.key_hash] = revoked
        return revoked

    def _consume(self, key: EmbedKey, now: datetime) -> None:
        bucket = self._buckets.get(key.key_hash)
        if bucket is None:
            bucket = self._buckets[key.key_hash] = _Bucket(
                key.rate_per_minute, now)
        elapsed = max(0.0, (now - bucket.ts).total_seconds())
        # negative elapsed (clock skew / replayed timestamps) refills nothing —
        # it must never DRAIN tokens either.
        bucket.tokens = min(float(key.rate_per_minute),
                            bucket.tokens + elapsed * key.rate_per_minute / 60.0)
        bucket.ts = now
        if bucket.tokens < 1.0:
            raise RateLimitExceeded(
                f"widget key {key.key_id} over {key.rate_per_minute}/min")
        bucket.tokens -= 1.0


def validate_request(keystore: KeyStore, *, raw_key: str, origin: str,
                     now: Optional[datetime] = None) -> EmbedKey:
    """Gate every widget call: key exists & active → origin allowed → bucket."""
    now = now or datetime.now(timezone.utc)
    key = keystore.get(raw_key)
    if key is None or not key.active(now):
        raise WidgetOriginError("unknown or revoked widget key")
    origin = (origin or "").rstrip("/")
    if origin not in [o.rstrip("/") for o in key.allowed_origins]:
        raise WidgetOriginError(
            f"origin {origin!r} not allowed for key {key.key_id}")
    keystore._consume(key, now)
    return key
