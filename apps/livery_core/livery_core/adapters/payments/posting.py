"""Invoice posting + affiliate commission automation (EBizCharge parity).

GA's EBizCharge module does two things operators rely on daily:
  1. A captured card payment posts straight onto the invoice (no re-keying).
  2. When that invoice belongs to a farmed trip, the affiliate commission
     is computed and queued for settlement automatically.

LiveryOS does the same through the event catalog: posting produces EVENTS
(invoice.paid, affiliate.commission_settled) that flow through the outbox —
the ERPNext Payment Entry writer and the affiliate ledger are subscribers.
Nothing here writes to ERPNext directly; the domain stays framework-free.

Money discipline: integer minor units; the commission split is delegated to
affiliates.engine.split_minor which guarantees partner + operator == gross.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from ...affiliates.engine import ENGINE_VERSION, Partner, split_minor
from ..events import Event


@dataclass(frozen=True)
class PaymentPosting:
    """What the ERPNext Payment Entry writer needs — nothing more."""
    tenant: str
    sales_invoice: str
    sales_order: str
    paid_minor: int
    currency: str
    gateway: str
    gateway_capture_ref: str
    mode_of_payment: str             # "Stripe" | "Paystack" — ERPNext MoP name
    paid_at: datetime
    idempotency_key: str


def post_payment_to_invoice(*, tenant: str, sales_invoice: str,
                            intent, paid_at: Optional[datetime] = None) -> "tuple[PaymentPosting, Event]":
    """Build the posting + invoice.paid event for a CAPTURED intent.

    Refuses to post anything that isn't a real capture — an AUTHORIZED hold
    is NOT revenue and never touches an invoice.
    """
    from .intent import IntentState  # local import: keeps module import-light

    if intent.state != IntentState.CAPTURED:
        raise ValueError(
            f"cannot post intent in state {intent.state.value} — "
            "only CAPTURED payments post to invoices"
        )
    paid_at = paid_at or datetime.now(timezone.utc)
    posting = PaymentPosting(
        tenant=tenant,
        sales_invoice=sales_invoice,
        sales_order=intent.sales_order,
        paid_minor=intent.captured_minor,
        currency=intent.currency,
        gateway=intent.gateway,
        gateway_capture_ref=intent.capture_ref,
        mode_of_payment="Stripe" if intent.gateway == "stripe" else "Paystack",
        paid_at=paid_at,
        idempotency_key=f"{intent.idempotency_key}:post",
    )
    event = Event("invoice.paid", tenant, paid_at, {
        "sales_invoice": sales_invoice,
        "sales_order": intent.sales_order,
        "paid_minor": intent.captured_minor,
        "currency": intent.currency,
        "gateway": intent.gateway,
        "gateway_capture_ref": intent.capture_ref,
        "idempotency_key": posting.idempotency_key,
    })
    return posting, event


@dataclass(frozen=True)
class CommissionPosting:
    """Affiliate commission for a captured farmed-trip payment."""
    tenant: str
    sales_order: str
    partner_ref: str
    gross_minor: int
    partner_minor: int
    operator_minor: int
    currency: str
    engine_version: str


def commission_for_captured_payment(*, tenant: str, intent,
                                    partner: Partner) -> "tuple[CommissionPosting, Event]":
    """Compute the partner commission for a captured, farmed payment.

    Per-booking-quote partners (split_pct is None) settle honestly outside
    this automation — the event says so explicitly instead of inventing a
    percentage.
    """
    from .intent import IntentState

    if intent.state != IntentState.CAPTURED:
        raise ValueError("commission requires a CAPTURED intent")
    gross = intent.captured_minor

    if partner.split_pct is None:
        event = Event("affiliate.commission_pending_quote", tenant,
                      datetime.now(timezone.utc), {
                          "sales_order": intent.sales_order,
                          "partner_ref": partner.partner_ref,
                          "gross_minor": gross, "currency": intent.currency,
                          "reason": "partner settles per-booking quote — no split_pct",
                      })
        posting = CommissionPosting(tenant, intent.sales_order,
                                    partner.partner_ref, gross, 0, gross,
                                    intent.currency, "quote-pending")
        return posting, event

    partner_minor, operator_minor = split_minor(gross, partner.split_pct)
    posting = CommissionPosting(
        tenant, intent.sales_order, partner.partner_ref, gross,
        partner_minor, operator_minor, intent.currency, ENGINE_VERSION,
    )
    assert posting.partner_minor + posting.operator_minor == gross  # zero penny loss
    event = Event("affiliate.commission_settled", tenant,
                  datetime.now(timezone.utc), {
                      "sales_order": intent.sales_order,
                      "partner_ref": partner.partner_ref,
                      "gross_minor": gross,
                      "partner_minor": partner_minor,
                      "operator_minor": operator_minor,
                      "currency": intent.currency,
                      "engine_version": ENGINE_VERSION,
                  })
    return posting, event
