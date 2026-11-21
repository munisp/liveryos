"""Adapters — thin Frappe/bench-facing wiring (report Ch. 5 §5.2.3).

booking_events / billing_events: translate DocType hooks into the canonical
event catalog (booking.created, trip.assigned, trip.status_changed,
invoice.paid) for the outbound webhook layer.

position_ingest: Traccar webhook intake + scheduled fallback poller
(Traccar → webhook → position store → Centrifugo → boards/apps).

compliance: daily chauffeur licence/medical expiry checks (gap-list item 12,
funded inside the ERPNext configuration workstream per Ch. 6).

transition/ga_adapter: the GA-shaped anti-corruption layer (Ch. 7 §7.4.2) —
decommissioning GA changes ONLY this module. Implemented in Phase 0 follow-on
once Gate 1 evidence lands (API vs CSV vs none).
"""
