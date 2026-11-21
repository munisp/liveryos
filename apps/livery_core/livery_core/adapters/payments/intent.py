"""Payment intent lifecycle — pre-auth now, capture on trip completion.

This is the GA "card-on-file + auth/capture" parity piece, built around the
platform's honesty rules:

- A booking may hold an AUTHORIZED intent (money reserved, not moved).
- Capture happens on COMPLETED (or NO_SHOW per policy) and may be for LESS
  than the authorization (meter adjustments) — never more.
- Refunds are their own transition, always against a captured intent.
- The state machine is explicit; illegal transitions raise, and the store
  keeps every intent so reconciliation sees the full history.

State machine:

    CREATED ──authorize──▶ AUTHORIZED ──capture──▶ CAPTURED ──refund──▶ REFUNDED
        │                      │
        └──cancel──▶ CANCELLED └──release──▶ RELEASED   (auth voided, funds freed)
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional
from uuid import uuid4

from .gateway import GatewayError, PaymentGateway


class IntentState(str, Enum):
    CREATED = "CREATED"
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    REFUNDED = "REFUNDED"
    RELEASED = "RELEASED"
    CANCELLED = "CANCELLED"


LEGAL_INTENT_TRANSITIONS = {
    IntentState.CREATED: {IntentState.AUTHORIZED, IntentState.CANCELLED},
    IntentState.AUTHORIZED: {IntentState.CAPTURED, IntentState.RELEASED},
    IntentState.CAPTURED: {IntentState.REFUNDED},
    IntentState.REFUNDED: set(),
    IntentState.RELEASED: set(),
    IntentState.CANCELLED: set(),
}

TERMINAL_INTENT_STATES = {IntentState.REFUNDED, IntentState.RELEASED,
                          IntentState.CANCELLED}


@dataclass(frozen=True)
class PaymentIntent:
    intent_id: str
    tenant: str
    sales_order: str                 # ERPNext SO this payment belongs to
    currency: str                    # ISO — fenced at the gateway
    authorized_minor: int
    state: IntentState = IntentState.CREATED
    captured_minor: int = 0
    refunded_minor: int = 0
    gateway: str = ""
    authorization_ref: str = ""
    capture_ref: str = ""
    idempotency_key: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def can(self, target: IntentState) -> bool:
        return target in LEGAL_INTENT_TRANSITIONS[self.state]

    def transition(self, target: IntentState, **changes) -> "PaymentIntent":
        if not self.can(target):
            raise GatewayError(
                f"illegal intent transition {self.state.value} → {target.value} "
                f"(intent {self.intent_id})"
            )
        return replace(self, state=target,
                       updated_at=datetime.now(timezone.utc), **changes)


class IntentStore:
    """In-memory intent store. Postgres persistence is a bench-side adapter
    (livery-db `payment_intents` table); the domain logic doesn't care."""

    def __init__(self):
        self._by_id: Dict[str, PaymentIntent] = {}
        self._by_idem: Dict[str, str] = {}

    def create(self, *, tenant: str, sales_order: str, currency: str,
               authorized_minor: int, idempotency_key: str) -> PaymentIntent:
        if authorized_minor <= 0:
            raise GatewayError("authorization amount must be positive minor units")
        if idempotency_key in self._by_idem:
            # Retried create returns the SAME intent — safe by construction.
            return self._by_id[self._by_idem[idempotency_key]]
        intent = PaymentIntent(uuid4().hex, tenant, sales_order,
                               currency.upper(), authorized_minor,
                               idempotency_key=idempotency_key)
        self._by_id[intent.intent_id] = intent
        self._by_idem[idempotency_key] = intent.intent_id
        return intent

    def get(self, intent_id: str) -> PaymentIntent:
        return self._by_id[intent_id]

    def save(self, intent: PaymentIntent) -> PaymentIntent:
        self._by_id[intent.intent_id] = intent
        return intent

    def for_sales_order(self, sales_order: str) -> "list[PaymentIntent]":
        return [i for i in self._by_id.values() if i.sales_order == sales_order]


def authorize_intent(store: IntentStore, gateway: PaymentGateway, *,
                     tenant: str, sales_order: str, amount_minor: int,
                     currency: str, customer_ref: str, card_token: str,
                     idempotency_key: str) -> PaymentIntent:
    """Create + authorize in one step (idempotent on idempotency_key)."""
    intent = store.create(tenant=tenant, sales_order=sales_order,
                          currency=currency, authorized_minor=amount_minor,
                          idempotency_key=idempotency_key)
    if intent.state != IntentState.CREATED:
        return intent  # replay — already authorized before
    res = gateway.authorize(amount_minor=amount_minor, currency=currency,
                            reference=sales_order, customer_ref=customer_ref,
                            card_token=card_token,
                            idempotency_key=f"{idempotency_key}:auth")
    if not res.ok:
        raise GatewayError(f"{gateway.name} authorization failed: {res.failure_reason}")
    return store.save(intent.transition(
        IntentState.AUTHORIZED, gateway=gateway.name,
        authorization_ref=res.reference))


def capture_on_completion(store: IntentStore, gateway: PaymentGateway, *,
                          intent_id: str, final_amount_minor: int,
                          idempotency_key: str) -> PaymentIntent:
    """Capture when the ride completes. Final may be ≤ authorized — NEVER more."""
    intent = store.get(intent_id)
    if intent.state == IntentState.CAPTURED:
        return intent  # replay-safe
    if final_amount_minor <= 0:
        raise GatewayError("capture amount must be positive minor units")
    if final_amount_minor > intent.authorized_minor:
        raise GatewayError(
            f"capture {final_amount_minor} exceeds authorization "
            f"{intent.authorized_minor} — re-authorize instead of over-capturing"
        )
    res = gateway.capture(authorization_ref=intent.authorization_ref,
                          amount_minor=final_amount_minor,
                          idempotency_key=f"{idempotency_key}:cap")
    if not res.ok:
        raise GatewayError(f"{gateway.name} capture failed: {res.failure_reason}")
    return store.save(intent.transition(
        IntentState.CAPTURED, captured_minor=final_amount_minor,
        capture_ref=res.reference))


def release_authorization(store: IntentStore, *, intent_id: str) -> PaymentIntent:
    """Void the hold (booking cancelled before completion)."""
    intent = store.get(intent_id)
    if intent.state == IntentState.RELEASED:
        return intent
    return store.save(intent.transition(IntentState.RELEASED))


def refund_capture(store: IntentStore, gateway: PaymentGateway, *,
                   intent_id: str, amount_minor: int,
                   idempotency_key: str) -> PaymentIntent:
    intent = store.get(intent_id)
    if intent.state == IntentState.REFUNDED:
        return intent
    if amount_minor <= 0 or amount_minor > intent.captured_minor:
        raise GatewayError("refund must be within captured amount")
    res = gateway.refund(capture_ref=intent.capture_ref,
                         amount_minor=amount_minor,
                         idempotency_key=f"{idempotency_key}:ref")
    if not res.ok:
        raise GatewayError(f"{gateway.name} refund failed: {res.failure_reason}")
    return store.save(intent.transition(IntentState.REFUNDED,
                                        refunded_minor=amount_minor))
