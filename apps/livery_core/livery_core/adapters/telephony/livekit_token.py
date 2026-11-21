"""LiveKit access-token builder (HS256 JWT, stdlib only).

LiveKit access tokens are JWTs with a `video` grant claim. The spec is
stable and public; signing is HMAC-SHA256 with the project's API secret.
We implement it with hashlib/hmac so the domain layer has zero third-party
dependencies — the token is verifiable by any stock LiveKit server.

A token proves IDENTITY + ROOM GRANT. It does not prove a call happened —
call records come from LiveKit webhooks, never from token issuance.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4


class NotConfiguredError(RuntimeError):
    """Missing LiveKit credentials — refuse to mint tokens."""


@dataclass(frozen=True)
class LiveKitConfig:
    api_key: Optional[str]
    api_secret: Optional[str]
    url: str = ""                    # wss://rtc.example.com — for clients
    token_ttl: timedelta = timedelta(minutes=30)

    def require(self) -> "LiveKitConfig":
        if not self.api_key or not self.api_secret:
            raise NotConfiguredError(
                "LiveKit api_key/api_secret not configured — "
                "voice features stay off, they never fake-connect")
        return self


@dataclass(frozen=True)
class VideoGrant:
    room: str
    room_join: bool = True
    can_publish: bool = True
    can_subscribe: bool = True
    room_admin: bool = False

    def to_claim(self) -> dict:
        return {
            "room": self.room,
            "roomJoin": self.room_join,
            "canPublish": self.can_publish,
            "canSubscribe": self.can_subscribe,
            "roomAdmin": self.room_admin,
        }


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def build_access_token(config: LiveKitConfig, *, identity: str,
                       grant: VideoGrant, name: str = "",
                       metadata: str = "",
                       now: Optional[datetime] = None) -> str:
    """Mint a LiveKit-compatible HS256 access token (real JWT, verifiable)."""
    config.require()
    if not identity:
        raise ValueError("identity required — anonymous riders are not a thing")
    now = now or datetime.now(timezone.utc)
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {
        "iss": config.api_key,
        "sub": identity,
        "jti": uuid4().hex,
        "iat": int(now.timestamp()),
        "nbf": int((now - timedelta(seconds=10)).timestamp()),
        "exp": int((now + config.token_ttl).timestamp()),
        "video": grant.to_claim(),
    }
    if name:
        claims["name"] = name
    if metadata:
        claims["metadata"] = metadata

    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode()) + "." +
        _b64url(json.dumps(claims, separators=(",", ":")).encode())
    )
    sig = hmac.new(config.api_secret.encode(), signing_input.encode(),
                   hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(sig)}"


def decode_unverified(token: str) -> dict:
    """Inspection helper for tests/tooling. UNVERIFIED — never authorize
    on this; LiveKit server does the real verification."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("not a JWT")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode()))


def room_for_booking(booking_ref: str, *, kind: str = "call") -> str:
    """Deterministic room naming — the same booking always maps to the same
    room, so reconnects rejoin rather than fork."""
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in booking_ref)
    return f"livery-{kind}-{safe}"
