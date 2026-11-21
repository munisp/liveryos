"""Outbox dispatcher tests — at-least-once, backoff, dead-letter (Ch. 5 §5.2.3)."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "livery_core"))

from livery_core.adapters.outbox import (  # noqa: E402
    DeliveryError, OutboxRow, backoff_seconds, dispatch_batch, webhook_deliver,
)

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


def row(eid, attempts=0):
    return OutboxRow(eid, "booking.created", "acme", {"x": 1}, NOW, attempts=attempts)


def test_backoff_caps_at_300():
    assert [backoff_seconds(i) for i in range(5)] == [1, 2, 4, 8, 16]
    assert backoff_seconds(20) == 300


def test_success_marks_published():
    sent = []
    rows = [row("e1"), row("e2")]
    s = dispatch_batch(rows, lambda r: sent.append(r.event_id))
    assert s.delivered == 2 and sent == ["e1", "e2"]
    assert all(r.published_at and not r.pending for r in rows)


def test_failure_retries_then_dead_letters():
    r = row("e1", attempts=7)   # one failure away from the 8-attempt ceiling
    s = dispatch_batch([r], lambda _: (_ for _ in ()).throw(DeliveryError("500")))
    assert s.dead_lettered == 1 and r.dead_lettered_at
    assert not r.pending and r.published_at is None
    # a dead-lettered row is never attempted again
    calls = []
    dispatch_batch([r], lambda _: calls.append(1))
    assert calls == []


def test_retryable_failure_keeps_pending():
    r = row("e1", attempts=2)
    s = dispatch_batch([r], lambda _: (_ for _ in ()).throw(DeliveryError("boom")))
    assert s.failed_retryable == 1 and r.pending and r.attempts == 3
    assert "in 8s" in s.errors[0]   # backoff hint for operators


def test_webhook_deliver_signs_and_raises_on_non_2xx():
    import hashlib
    import hmac
    import json
    seen = {}

    def fake_transport(url, headers, body):
        seen.update(headers=headers, raw=body)
        return 200

    deliver = webhook_deliver("https://sub.example/hook", "s3cret", transport=fake_transport)
    deliver(row("evt-9"))
    # signature must match the exact bytes sent
    expected = hmac.new(b"s3cret", seen["raw"], hashlib.sha256).hexdigest()
    assert seen["headers"]["X-LiveryOS-Signature"] == f"sha256={expected}"
    assert json.loads(seen["raw"])["event_id"] == "evt-9"   # dedupe contract visible

    def failing(url, headers, body):
        return 500
    import pytest
    with pytest.raises(DeliveryError):
        webhook_deliver("https://sub.example/hook", "k", transport=failing)(row("e1"))
