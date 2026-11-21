-- LiveryOS platform Postgres — first-boot provisioning (docker-entrypoint-initdb.d).
-- One instance, database-per-concern. livery_core is the LiveryOS-owned
-- platform schema; keycloak/traccar are satellite-owned schemas for the
-- respective services (their own migration tooling manages the tables).

CREATE DATABASE livery_core;
CREATE DATABASE keycloak;
CREATE DATABASE traccar;

\c livery_core

-- Everything below is owned by livery_core migrations; this file creates
-- the Phase 2 baseline so a fresh bench is usable immediately.

-- Tenant registry (scripts/new-tenant.sh also appends jsonl; this is the
-- system of record once the registry writer lands).
CREATE TABLE IF NOT EXISTS tenant_registry (
    slug            TEXT PRIMARY KEY,
    market          TEXT NOT NULL CHECK (market IN ('PT', 'NG')),
    site            TEXT NOT NULL,
    currency        TEXT NOT NULL CHECK (currency IN ('EUR', 'NGN')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Event outbox (report Ch. 5 §5.2.3): producers insert, the dispatcher
-- drains to subscribers at-least-once; subscribers dedupe on event_id.
-- booking_events._emit writes HERE instead of log-only once wired.
CREATE TABLE IF NOT EXISTS event_outbox (
    event_id        UUID PRIMARY KEY,
    type            TEXT NOT NULL,
    tenant          TEXT NOT NULL REFERENCES tenant_registry(slug),
    occurred_at     TIMESTAMPTZ NOT NULL,
    payload         JSONB NOT NULL,
    schema_version  INT NOT NULL DEFAULT 1,
    published_at    TIMESTAMPTZ,               -- NULL = pending delivery
    attempts        INT NOT NULL DEFAULT 0,
    last_error      TEXT
);
CREATE INDEX IF NOT EXISTS event_outbox_pending ON event_outbox (tenant) WHERE published_at IS NULL;

-- Position store (latest fix per device; history ring is the trailing
-- partition per device — consumers read latest via DISTINCT ON).
CREATE TABLE IF NOT EXISTS position_fixes (
    id              BIGSERIAL PRIMARY KEY,
    tenant          TEXT NOT NULL,
    device_id       TEXT NOT NULL,
    driver_id       TEXT,
    ride_id         TEXT,
    lat             DOUBLE PRECISION NOT NULL CHECK (lat BETWEEN -90 AND 90),
    lng             DOUBLE PRECISION NOT NULL CHECK (lng BETWEEN -180 AND 180),
    speed_kmh       REAL NOT NULL DEFAULT 0,
    heading         REAL NOT NULL DEFAULT 0,
    recorded_at     TIMESTAMPTZ NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('traccar_webhook', 'traccar_poll')),
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant, device_id, recorded_at)    -- out-of-order/duplicate guard
);
CREATE INDEX IF NOT EXISTS position_fixes_latest
    ON position_fixes (tenant, device_id, recorded_at DESC);

-- GA import staging (Ch. 7 §7.4.2 / Ch. 8 reconciliation): every imported
-- row lands here BEFORE becoming a native booking, with provenance and the
-- quarantine trail the parallel-run reconciliation report reads.
CREATE TABLE IF NOT EXISTS ga_import_rows (
    id              BIGSERIAL PRIMARY KEY,
    tenant          TEXT NOT NULL,
    batch_id        UUID NOT NULL,
    row_no          INT NOT NULL,
    raw             JSONB NOT NULL,
    external_id     TEXT,                       -- GA reservation number
    status          TEXT NOT NULL CHECK (status IN ('imported', 'quarantined')),
    error           TEXT,                       -- quarantine reason
    canonical       JSONB,                      -- CanonicalBooking when imported
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ga_import_batch ON ga_import_rows (tenant, batch_id);

-- Affiliate engine seed (Phase 3 builds on this): farm-in/farm-out partner
-- terms, GRES-shaped so "GNet partner" is configuration, not a special case.
CREATE TABLE IF NOT EXISTS affiliate_partners (
    id              BIGSERIAL PRIMARY KEY,
    tenant          TEXT NOT NULL,
    partner_ref     TEXT NOT NULL,              -- Supplier ref on the bench
    direction       TEXT NOT NULL CHECK (direction IN ('farm_in', 'farm_out', 'both')),
    split_pct       NUMERIC(5,2),               -- partner's share; NULL = per-booking
    blocked_routes  JSONB NOT NULL DEFAULT '[]',-- blocking lists (airports, zones)
    gres_system_id  TEXT,                       -- GNet system id once connected
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant, partner_ref)
);
