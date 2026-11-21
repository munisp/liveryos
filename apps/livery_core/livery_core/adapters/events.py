"""Canonical event catalog (report Ch. 5 §5.2.3).

Every meaningful business occurrence becomes one of these shapes. The
outbound webhook layer, the GA transition adapter, and (Phase 3) the GNet
affiliate payloads all consume the SAME shapes — so decommissioning GA or
adding a network partner never touches event producers.

Money stays integer minor units + ISO currency. Timestamps are UTC ISO-8601.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

EVENT_TYPES = (
    "booking.created",
    "booking.cancelled",
    "trip.assigned",
    "trip.status_changed",
    "invoice.issued",
    "invoice.paid",
)


@dataclass(frozen=True)
class Event:
    type: str                             # one of EVENT_TYPES
    tenant: str
    occurred_at: datetime
    payload: dict
    event_id: str = field(default_factory=lambda: uuid4().hex)
    schema_version: int = 1

    def to_dict(self) -> dict:
        d = asdict(self)
        d["occurred_at"] = self.occurred_at.isoformat()
        return d


def _now() -> datetime:
    return datetime.now(timezone.utc)


def booking_created(tenant: str, *, sales_order: str, customer: str,
                    pickup_at: datetime, service_class: str,
                    quoted_minor: int, currency: str,
                    occurred_at: Optional[datetime] = None) -> Event:
    return Event("booking.created", tenant, occurred_at or _now(), {
        "sales_order": sales_order, "customer": customer,
        "pickup_at": pickup_at.isoformat(), "service_class": service_class,
        "quoted_minor": quoted_minor, "currency": currency,
    })


def booking_cancelled(tenant: str, *, sales_order: str, reason: str = "",
                      occurred_at: Optional[datetime] = None) -> Event:
    return Event("booking.cancelled", tenant, occurred_at or _now(), {
        "sales_order": sales_order, "reason": reason,
    })


def trip_assigned(tenant: str, *, ride: str, driver: str, vehicle: str = "",
                  eta_minutes: Optional[int] = None, eta_source: str = "estimate",
                  occurred_at: Optional[datetime] = None) -> Event:
    # eta_source is mandatory provenance: "estimate" (straight-line) or
    # "valhalla" (routed). Never emit an ETA without its source.
    return Event("trip.assigned", tenant, occurred_at or _now(), {
        "ride": ride, "driver": driver, "vehicle": vehicle,
        "eta_minutes": eta_minutes, "eta_source": eta_source,
    })


def trip_status_changed(tenant: str, *, ride: str, from_status: str,
                        to_status: str,
                        occurred_at: Optional[datetime] = None) -> Event:
    return Event("trip.status_changed", tenant, occurred_at or _now(), {
        "ride": ride, "from": from_status, "to": to_status,
    })


def invoice_issued(tenant: str, *, sales_invoice: str, customer: str,
                   total_minor: int, currency: str,
                   occurred_at: Optional[datetime] = None) -> Event:
    return Event("invoice.issued", tenant, occurred_at or _now(), {
        "sales_invoice": sales_invoice, "customer": customer,
        "total_minor": total_minor, "currency": currency,
    })


def invoice_paid(tenant: str, *, payment_entry: str, sales_invoice: str = "",
                 paid_minor: int = 0, currency: str = "", mode: str = "",
                 occurred_at: Optional[datetime] = None) -> Event:
    return Event("invoice.paid", tenant, occurred_at or _now(), {
        "payment_entry": payment_entry, "sales_invoice": sales_invoice,
        "paid_minor": paid_minor, "currency": currency, "mode_of_payment": mode,
    })


def to_minor(amount, currency: str = "EUR") -> int:
    """DocType currency fields are Decimal/float major units; the catalog is
    minor units. EUR/NGN are both 2-decimal — assert if that ever changes."""
    from decimal import Decimal, ROUND_HALF_UP
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
