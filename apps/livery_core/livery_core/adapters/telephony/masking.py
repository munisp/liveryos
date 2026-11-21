"""Phone-number masking (GA parity: their $30/mo add-on).

Passenger and driver never see each other's real numbers. A call/SMS to the
proxy number is bridged by the carrier/LiveKit SIP layer to the real party
for as long as the assignment is active.

Rules:
- One active assignment per (booking, party) pair; a proxy number is never
  shared by two active assignments (privacy + billing disputes).
- Assignments expire (default: trip end + 2h). Releasing early is explicit.
- The pool is injected (rows from livery-db `telephony_proxy_numbers`);
  if the pool is empty we raise NotProvisionedError — the caller renders
  "calling unavailable", it does NOT dial the real number as a fallback
  (that would silently un-mask; un-masking must be a deliberate operation).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, Optional


class NotProvisionedError(RuntimeError):
    """No proxy number available — surface 'unavailable', never fall back
    to a real number silently."""


class NumberInUseError(RuntimeError):
    """Attempted to assign a number that is still active elsewhere."""


DEFAULT_TTL = timedelta(hours=2)


@dataclass(frozen=True)
class MaskingAssignment:
    booking_ref: str
    party: str                    # "passenger" | "driver"
    proxy_number: str             # E.164
    real_number: str              # E.164 — stored for bridging, never shown
    assigned_at: datetime
    expires_at: datetime
    released_at: Optional[datetime] = None

    def active(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return self.released_at is None and self.expires_at > now


class MaskingPool:
    """In-memory pool with the same semantics the Postgres adapter enforces
    (unique partial index on active proxy_number)."""

    def __init__(self, numbers: Iterable[str]):
        self._available: "list[str]" = sorted(set(numbers))
        self._active: Dict[str, MaskingAssignment] = {}   # proxy → assignment

    def assign(self, *, booking_ref: str, party: str, real_number: str,
               now: Optional[datetime] = None,
               ttl: timedelta = DEFAULT_TTL) -> MaskingAssignment:
        if party not in ("passenger", "driver"):
            raise ValueError(f"unknown party {party!r}")
        if not real_number:
            raise ValueError("real_number required — masking without a target "
                             "would bridge calls to nowhere")
        now = now or datetime.now(timezone.utc)
        self._sweep_expired(now)
        if not self._available:
            raise NotProvisionedError(
                f"proxy pool exhausted for booking {booking_ref}")
        proxy = self._available.pop(0)
        a = MaskingAssignment(booking_ref, party, proxy, real_number,
                              assigned_at=now, expires_at=now + ttl)
        self._active[proxy] = a
        return a

    def release(self, assignment: MaskingAssignment,
                now: Optional[datetime] = None) -> MaskingAssignment:
        now = now or datetime.now(timezone.utc)
        current = self._active.get(assignment.proxy_number)
        if current is None:
            return assignment  # already released/expired — idempotent
        released = replace(current, released_at=now)
        del self._active[current.proxy_number]
        self._available.append(current.proxy_number)
        self._available.sort()
        return released

    def active_for_booking(self, booking_ref: str,
                           now: Optional[datetime] = None) -> "list[MaskingAssignment]":
        now = now or datetime.now(timezone.utc)
        return [a for a in self._active.values()
                if a.booking_ref == booking_ref and a.active(now)]

    def dial_target(self, proxy_number: str,
                    now: Optional[datetime] = None) -> str:
        """Bridge lookup: which real number does this proxy ring RIGHT NOW?
        Expired/released proxies raise — the SIP layer must not guess."""
        a = self._active.get(proxy_number)
        if a is None or not a.active(now):
            raise NotProvisionedError(
                f"proxy {proxy_number} has no active assignment")
        return a.real_number

    def _sweep_expired(self, now: datetime) -> None:
        for proxy, a in list(self._active.items()):
            if not a.active(now):
                del self._active[proxy]
                self._available.append(proxy)
        self._available.sort()

    # Guard against the classic bug: handing out a number still active.
    def assign_specific(self, proxy_number: str, **kwargs) -> MaskingAssignment:
        if proxy_number in self._active and self._active[proxy_number].active():
            raise NumberInUseError(f"{proxy_number} still active elsewhere")
        if proxy_number not in self._available:
            raise NotProvisionedError(f"{proxy_number} not in pool")
        now = kwargs.pop("now", None) or datetime.now(timezone.utc)
        self._available.remove(proxy_number)
        ttl = kwargs.pop("ttl", DEFAULT_TTL)
        a = MaskingAssignment(proxy_number=proxy_number,
                              assigned_at=now, expires_at=now + ttl, **kwargs)
        self._active[proxy_number] = a
        return a
