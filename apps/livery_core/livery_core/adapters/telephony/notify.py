"""Notification composer — SMS / WhatsApp / voice-drop payloads.

Turns canonical catalog events into channel-specific notification requests.
Delivery is NOT done here: composed requests are queued into the event
outbox (at-least-once + dead-letter) and a worker with provider
credentials (Twilio-class SMS aggregator, WhatsApp Business API, LiveKit
SIP for voice drops) sends them. This module decides WHAT to send and to
WHOM — and is honest that nothing has been sent yet.

Channel policy is market-driven:
- NG (Nigeria): WhatsApp-first (it's what riders actually read), SMS as
  fallback, voice drop for critical alerts only.
- PT (Portugal): SMS-first, WhatsApp optional, voice for critical only.

Every message renders from a named template with the booking reference —
no free-form AI text to riders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from string import Template
from typing import Optional

from ..events import Event


class Channel(str, Enum):
    WHATSAPP = "whatsapp"
    SMS = "sms"
    VOICE = "voice"


@dataclass(frozen=True)
class NotificationRequest:
    """One queued notification. `status` starts 'queued' — only the
    delivery worker may ever write 'sent'."""
    tenant: str
    channel: Channel
    to: str                        # E.164
    template: str
    body: str
    related_event_id: str
    related_booking: str
    status: str = "queued"         # queued → sent | failed (worker-owned)


_TEMPLATES = {
    "booking.created": Template(
        "Booking $booking confirmed. Pickup $pickup_at. Ref $booking."),
    "trip.assigned": Template(
        "Your driver $driver is on the way. Ref $booking."),
    "trip.status_changed": Template(
        "Ride update: $status. Ref $booking."),
    "booking.cancelled": Template(
        "Booking $booking was cancelled. $reason"),
    "invoice.paid": Template(
        "Payment received for $booking — thank you."),
    "trip.rebooked": Template(
        "Your pickup was adjusted to $pickup_at (flight $flight). Ref $booking."),
    "flight.status_changed": Template(
        "Flight $flight: $status. We're tracking it. Ref $booking."),
}

# Events that justify a voice drop when the passenger hasn't read anything.
_CRITICAL_EVENTS = {"booking.cancelled", "trip.rebooked"}


def default_channel_policy(market: str, event_type: str,
                           whatsapp_opted_in: bool = True) -> "list[Channel]":
    """Ordered channel fallback. First channel the worker can deliver wins;
    the rest are fallback, not duplicates."""
    market = market.upper()
    primary_first = [Channel.WHATSAPP, Channel.SMS] if market == "NG" \
        else [Channel.SMS, Channel.WHATSAPP]
    if not whatsapp_opted_in:
        primary_first = [c for c in primary_first if c != Channel.WHATSAPP]
        if not primary_first:
            primary_first = [Channel.SMS]
    if event_type in _CRITICAL_EVENTS:
        primary_first.append(Channel.VOICE)
    return primary_first


def _render(event_type: str, payload: dict) -> "tuple[str, str]":
    template = _TEMPLATES.get(event_type)
    if template is None:
        raise ValueError(f"no rider template for event {event_type!r} — "
                         "add one explicitly; no free-form text to riders")
    body = template.safe_substitute(
        booking=payload.get("sales_order", payload.get("booking", "")),
        pickup_at=payload.get("pickup_at", ""),
        driver=payload.get("driver", "your driver"),
        status=payload.get("status", payload.get("to", "")),
        reason=payload.get("reason", ""),
        flight=payload.get("flight", ""),
    )
    return body, f"tmpl_{event_type.replace('.', '_')}"


def compose_for_event(event: Event, *, market: str, to: str,
                      whatsapp_opted_in: bool = True,
                      channels: "Optional[list[Channel]]" = None) -> "list[NotificationRequest]":
    """Compose the channel-ordered notification queue for one event.

    Returns requests in fallback order. The worker tries in order and stops
    at first successful delivery (they are fallbacks, not fan-out).
    """
    if event.type not in _TEMPLATES:
        return []                    # not a rider-facing event — nothing to send
    if not to:
        raise ValueError("recipient number required")
    body, tmpl = _render(event.type, event.payload)
    booking = str(event.payload.get("sales_order",
                                    event.payload.get("booking", "")))
    order = channels or default_channel_policy(market, event.type,
                                               whatsapp_opted_in)
    return [NotificationRequest(
        tenant=event.tenant, channel=ch, to=to, template=tmpl, body=body,
        related_event_id=event.event_id, related_booking=booking,
    ) for ch in order]
