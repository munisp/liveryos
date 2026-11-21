"""Billing event hook targets (Sales Invoice / Payment Entry → catalog).

Thin wiring only — no business rules. Same honest-degrade emit path as
booking_events until the outbox DocType lands.
"""

from __future__ import annotations

from . import events
from .booking_events import _emit, frappe_site_slug


def on_submit(doc, method=None):  # pragma: no cover — bench only
    """Sales Invoice submitted → invoice.issued."""
    event = events.invoice_issued(
        tenant=frappe_site_slug(),
        sales_invoice=doc.name,
        customer=doc.customer,
        total_minor=events.to_minor(doc.grand_total, doc.currency),
        currency=doc.currency,
    )
    _emit(event)


def on_payment(doc, method=None):  # pragma: no cover — bench only
    """Payment Entry submitted → invoice.paid (per referenced invoice when
    present; the platform never blends currencies, so the payment currency
    is asserted against the invoice currency)."""
    invoice = ""
    for ref in getattr(doc, "references", []) or []:
        if getattr(ref, "reference_doctype", "") == "Sales Invoice":
            invoice = ref.reference_name
            break
    event = events.invoice_paid(
        tenant=frappe_site_slug(),
        payment_entry=doc.name,
        sales_invoice=invoice,
        paid_minor=events.to_minor(doc.paid_amount, doc.paid_from_account_currency),
        currency=doc.paid_from_account_currency,
        mode=getattr(doc, "mode_of_payment", "") or "",
    )
    _emit(event)
