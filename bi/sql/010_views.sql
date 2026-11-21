-- LiveryOS BI views (reporting/BI workstream).
--
-- Metabase connects to livery-db with a READ-ONLY user (boundary-service
-- rule: unmodified AGPL binary, read-only credentials, never writes back).
-- These views are the ONLY surface Metabase sees. Everything is per-tenant.
--
-- Money discipline: revenue views are grouped BY currency and NEVER sum
-- across currencies. EUR and NGN rows live side by side, unblended —
-- exactly as the platform stores them.

-- Bookings per day (from the event catalog — the single source of truth).
CREATE OR REPLACE VIEW v_bi_bookings_per_day AS
SELECT
    tenant,
    date_trunc('day', occurred_at)::date AS day,
    count(*)                             AS bookings,
    count(*) FILTER (WHERE payload->>'service_class' = 'executive') AS executive,
    count(*) FILTER (WHERE payload->>'service_class' = 'sprinter')  AS sprinter
FROM event_outbox
WHERE type = 'booking.created'
GROUP BY tenant, day
ORDER BY day DESC;

-- Revenue per day per currency — NO cross-currency totals, by design.
CREATE OR REPLACE VIEW v_bi_revenue_per_day AS
SELECT
    tenant,
    date_trunc('day', occurred_at)::date AS day,
    payload->>'currency'                 AS currency,
    count(*)                             AS paid_invoices,
    sum((payload->>'paid_minor')::bigint) AS revenue_minor
FROM event_outbox
WHERE type = 'invoice.paid'
GROUP BY tenant, day, payload->>'currency'
ORDER BY day DESC;

-- Affiliate settlements per partner (commission automation stream).
CREATE OR REPLACE VIEW v_bi_affiliate_settlements AS
SELECT
    tenant,
    payload->>'partner_ref'              AS partner_ref,
    date_trunc('week', occurred_at)::date AS week,
    payload->>'currency'                 AS currency,
    sum((payload->>'gross_minor')::bigint)    AS gross_minor,
    sum((payload->>'partner_minor')::bigint)  AS partner_minor,
    sum((payload->>'operator_minor')::bigint) AS operator_minor
FROM event_outbox
WHERE type = 'affiliate.commission_settled'
GROUP BY tenant, payload->>'partner_ref', week, payload->>'currency';

-- Payment funnel: authorized vs captured vs refunded per gateway.
CREATE OR REPLACE VIEW v_bi_payment_funnel AS
SELECT
    tenant,
    gateway,
    currency,
    count(*) FILTER (WHERE state IN ('AUTHORIZED','CAPTURED','REFUNDED')) AS authorized,
    count(*) FILTER (WHERE state = 'CAPTURED')  AS captured,
    count(*) FILTER (WHERE state = 'REFUNDED')  AS refunded,
    count(*) FILTER (WHERE state = 'RELEASED')  AS released_auths,
    sum(captured_minor)                          AS captured_minor_total
FROM payment_intents
GROUP BY tenant, gateway, currency;

-- Fleet activity: distinct devices reporting per hour (driver utilization).
CREATE OR REPLACE VIEW v_bi_fleet_activity AS
SELECT
    tenant,
    date_trunc('hour', recorded_at) AS hour,
    count(DISTINCT device_id)       AS active_devices,
    count(*)                        AS fixes
FROM position_fixes
GROUP BY tenant, hour
ORDER BY hour DESC;

-- Outbox health (operations): pending backlog + dead letters per tenant.
-- Dead letters are an operator queue — this view is the morning checklist.
CREATE OR REPLACE VIEW v_bi_outbox_health AS
SELECT
    tenant,
    count(*) FILTER (WHERE published_at IS NULL
                     AND dead_lettered_at IS NULL)            AS pending,
    count(*) FILTER (WHERE dead_lettered_at IS NOT NULL)      AS dead_letters,
    max(occurred_at) FILTER (WHERE published_at IS NULL)      AS oldest_pending
FROM event_outbox
GROUP BY tenant;

-- GA migration health: import vs quarantine per tenant (parallel-run era).
CREATE OR REPLACE VIEW v_bi_ga_import_health AS
SELECT
    tenant,
    batch_id,
    count(*) FILTER (WHERE status = 'imported')    AS imported,
    count(*) FILTER (WHERE status = 'quarantined') AS quarantined,
    round(100.0 * count(*) FILTER (WHERE status = 'quarantined')
          / nullif(count(*), 0), 2)                AS quarantine_pct
FROM ga_import_rows
GROUP BY tenant, batch_id;

-- Flight tracking effectiveness: what the feed did to pickups.
CREATE OR REPLACE VIEW v_bi_flight_adjustments AS
SELECT
    tenant,
    date_trunc('day', occurred_at)::date AS day,
    count(*) FILTER (WHERE type = 'trip.rebooked')          AS auto_shifts,
    count(*) FILTER (WHERE type = 'flight.status_changed')  AS alerts
FROM event_outbox
WHERE type IN ('trip.rebooked', 'flight.status_changed')
GROUP BY tenant, day
ORDER BY day DESC;
