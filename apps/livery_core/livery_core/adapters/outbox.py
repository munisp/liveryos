"""Event outbox dispatcher (report Ch. 5 §5.2.3; completes the Phase 2 seam).

The outbox table (event_outbox, infra/postgres-init.sql) is the guarantee:
producers INSERT in the same transaction as the business change; this
dispatcher drains pending rows to subscribers. Delivery is AT-LEAST-ONCE —
subscribers dedupe on event_id (the contract is printed in the payload).

Failure discipline:
- Exponential backoff with a 300s cap (1s, 2s, 4s, …, 300s).
- After max_attempts the row is dead-lettered — NOT dropped. Dead letters
  are an operator queue (someone looks every morning), never a silent loss.
- A row is only marked published AFTER the subscriber acks; a crash
  mid-publish replays the event (hence at-least-once, hence dedupe).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional


MAX_BACKOFF_SECONDS = 300
DEFAULT_MAX_ATTEMPTS = 8


class DeliveryError(RuntimeError):
    """Subscriber did not ack — the dispatcher retries per backoff."""


@dataclass
class OutboxRow:
    event_id: str
    type: str
    tenant: str
    payload: dict
    occurred_at: datetime
    attempts: int = 0
    last_error: Optional[str] = None
    published_at: Optional[datetime] = None
    dead_lettered_at: Optional[datetime] = None

    @property
    def pending(self) -> bool:
        return self.published_at is None and self.dead_lettered_at is None


def backoff_seconds(attempts: int) -> int:
    """1, 2, 4, 8, … capped at 300s. attempts counts prior failures."""
    return min(2 ** max(0, attempts), MAX_BACKOFF_SECONDS)


Deliver = Callable[[OutboxRow], None]  # raise DeliveryError on non-ack


@dataclass
class DispatchSummary:
    delivered: int = 0
    failed_retryable: int = 0
    dead_lettered: int = 0
    errors: list[str] = field(default_factory=list)


def dispatch_batch(rows: list[OutboxRow], deliver: Deliver,
                   max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                   now: Optional[datetime] = None) -> DispatchSummary:
    """One drain cycle over pending rows. Rows own their state transitions:
    published / retryable-with-backoff / dead-lettered."""
    now = now or datetime.now(timezone.utc)
    summary = DispatchSummary()
    for row in rows:
        if not row.pending:
            continue
        try:
            deliver(row)
        except DeliveryError as e:
            row.attempts += 1
            row.last_error = str(e)
            if row.attempts >= max_attempts:
                row.dead_lettered_at = now
                summary.dead_lettered += 1
                summary.errors.append(f"dead-letter {row.event_id} after {row.attempts}: {e}")
            else:
                summary.failed_retryable += 1
                summary.errors.append(
                    f"retry {row.event_id} in {backoff_seconds(row.attempts)}s: {e}")
            continue
        row.published_at = now
        summary.delivered += 1
    return summary


def webhook_deliver(url: str, secret: str,
                    transport: Optional[Callable] = None) -> Deliver:
    """Build a deliver() that POSTs the event as a subscriber webhook with
    an HMAC signature header (subscribers verify + dedupe on event_id).
    Non-2xx → DeliveryError (retryable)."""
    import hashlib
    import hmac
    import json
    import urllib.request

    def _default_transport(u: str, headers: dict, body: bytes) -> int:
        req = urllib.request.Request(u, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status

    send = transport or _default_transport

    def deliver(row: OutboxRow) -> None:
        body = json.dumps({
            "event_id": row.event_id,          # dedupe key — subscriber contract
            "type": row.type, "tenant": row.tenant,
            "occurred_at": row.occurred_at.isoformat(),
            "payload": row.payload,
        }).encode()
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        status = send(url, {"Content-Type": "application/json",
                            "X-LiveryOS-Signature": f"sha256={sig}"}, body)
        if not (200 <= status < 300):
            raise DeliveryError(f"subscriber returned {status}")

    return deliver
