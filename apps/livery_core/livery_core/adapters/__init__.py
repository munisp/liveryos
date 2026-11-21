"""Adapters — thin Frappe/bench-facing wiring (report Ch. 5 §5.2.3).

events: canonical event catalog shapes (booking.created, booking.cancelled,
trip.assigned, trip.status_changed, invoice.issued, invoice.paid) — one
shape consumed by webhooks, the GA adapter, and Phase 3 GNet payloads.

booking_events / billing_events: DocType hook targets that translate
Sales Order / Sales Invoice / Payment Entry lifecycle into catalog events.
Emit path is log-only until the Livery Event Outbox DocType lands (Phase 2
bench integration) — honest degrade, nothing claims to have been "sent".

position_ingest: Traccar webhook intake + scheduled fallback poller
(Traccar → webhook → position store → Centrifugo → boards/apps).

compliance: daily chauffeur licence/medical expiry sweep (gap-list item 12,
funded inside the ERPNext configuration workstream per Ch. 6). Feeds
dispatch.board DriverState.compliance_valid.

transition/ga_adapter: the GA-shaped anti-corruption layer (Ch. 7 §7.4.2) —
CSV export path live now; API path raises NotConfigured until Gate 1
delivers documented API + sandbox. Decommissioning GA changes ONLY this
package.
"""
