"""Booking event hook targets (Sales Order submit/cancel → catalog events).

Thin wiring: extract fields from the DocType, build the canonical event,
hand it to the outbound webhook outbox. No business rules here.
"""

from __future__ import annotations

from . import events


def _emit(event):  # pragma: no cover — bench only
    """Outbound delivery target. A dedicated "Livery Event Outbox" DocType
    (append → scheduled dispatcher → subscriber endpoints, at-least-once
    with event_id dedupe) lands with the console API in Phase 2 bench
    integration. Until then events are logged — honest degrade: nothing
    claims to have been "sent"."""
    import frappe  # noqa: PLC0415
    frappe.logger("livery.events").info(event.to_dict())


def on_submit(doc, method=None):  # pragma: no cover — bench only
    from datetime import datetime
    pickup_at = getattr(doc, "livery_pickup_at", None) or datetime.now()
    if isinstance(pickup_at, str):
        pickup_at = datetime.fromisoformat(pickup_at)
    event = events.booking_created(
        tenant=frappe_site_slug(),
        sales_order=doc.name,
        customer=doc.customer,
        pickup_at=pickup_at,
        service_class=getattr(doc, "livery_service_class", "sedan"),
        quoted_minor=events.to_minor(doc.grand_total, doc.currency),
        currency=doc.currency,
    )
    _emit(event)


def on_cancel(doc, method=None):  # pragma: no cover — bench only
    event = events.booking_cancelled(
        tenant=frappe_site_slug(),
        sales_order=doc.name,
        reason=getattr(doc, "livery_cancel_reason", "") or "",
    )
    _emit(event)


def frappe_site_slug() -> str:  # pragma: no cover — bench only
    import frappe  # noqa: PLC0415
    return frappe.local.site.split(".")[0]
