"""Frappe hooks — thin wiring only. Business rules live in framework-free modules."""

app_name = "livery_core"
app_title = "LiveryOS Core"
app_publisher = "LiveryOS"
app_description = "Custom operational core: rate engine, dispatch board, affiliate engine, GA transition adapter"
app_email = "ops@liveryos.local"
app_license = "Proprietary"  # custom app — separately licensable from GPL-3.0 ERPNext (report Ch. 9 §9.2.1; counsel flag open)

required_apps = ["erpnext"]

# DocType CRUD events → domain events (report Ch. 5 §5.2.3 event catalog).
# Adapter modules translate DocType payloads into the canonical event shapes
# (booking.created, trip.assigned, trip.status_changed, invoice.paid) and push
# them onto the outbound webhook layer. No rules here.
doc_events = {
    "Sales Order": {
        "on_submit": "livery_core.adapters.booking_events.on_submit",
        "on_cancel": "livery_core.adapters.booking_events.on_cancel",
    },
    "Sales Invoice": {
        "on_submit": "livery_core.adapters.billing_events.on_submit",
    },
    "Payment Entry": {
        "on_submit": "livery_core.adapters.billing_events.on_payment",
    },
}

# Scheduled jobs (report Ch. 2 §2.2.8 — ERPNext's scheduler is Class A substrate)
scheduler_events = {
    "hourly": [
        "livery_core.adapters.position_ingest.poll_traccar_fallback",  # fallback only; webhooks are primary
    ],
    "daily": [
        "livery_core.adapters.compliance.check_chauffeur_expiry",      # gap item 12: licence/medical expiry alerts
    ],
}
