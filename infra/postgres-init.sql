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
    last_error      TEXT,
    dead_lettered_at TIMESTAMPTZ               -- set at max attempts; NEVER dropped
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

-- ─── Phase 6 tables ────────────────────────────────────────────────────────

-- Payment intents (payments breadth): one row per intent; state machine
-- enforced by livery_core.adapters.payments.intent. Gateway refs let
-- reconciliation re-derive every capture from the provider.
CREATE TABLE IF NOT EXISTS payment_intents (
    intent_id       UUID PRIMARY KEY,
    tenant          TEXT NOT NULL,
    sales_order     TEXT NOT NULL,
    currency        TEXT NOT NULL CHECK (currency IN ('EUR', 'NGN')),
    authorized_minor BIGINT NOT NULL CHECK (authorized_minor > 0),
    captured_minor  BIGINT NOT NULL DEFAULT 0 CHECK (captured_minor >= 0),
    refunded_minor  BIGINT NOT NULL DEFAULT 0 CHECK (refunded_minor >= 0),
    state           TEXT NOT NULL CHECK (state IN
        ('CREATED','AUTHORIZED','CAPTURED','REFUNDED','RELEASED','CANCELLED')),
    gateway         TEXT NOT NULL DEFAULT '',
    authorization_ref TEXT NOT NULL DEFAULT '',
    capture_ref     TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT NOT NULL UNIQUE,       -- replay-safe by construction
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (captured_minor <= authorized_minor),  -- capture never exceeds auth
    CHECK (refunded_minor <= captured_minor)
);
CREATE INDEX IF NOT EXISTS payment_intents_tenant_state
    ON payment_intents (tenant, state);

-- Telephony masking assignments (masked passenger↔driver calls).
-- Partial unique index = a proxy number has AT MOST one live assignment.
CREATE TABLE IF NOT EXISTS telephony_masking_assignments (
    id              BIGSERIAL PRIMARY KEY,
    tenant          TEXT NOT NULL,
    booking_ref     TEXT NOT NULL,
    party           TEXT NOT NULL CHECK (party IN ('passenger', 'driver')),
    proxy_number    TEXT NOT NULL,
    real_number     TEXT NOT NULL,              -- bridging only, never displayed
    assigned_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    released_at     TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS masking_one_active_per_number
    ON telephony_masking_assignments (proxy_number)
    WHERE released_at IS NULL;

-- Widget embed keys: raw key is NEVER stored, only its sha256.
CREATE TABLE IF NOT EXISTS widget_embed_keys (
    key_id          TEXT PRIMARY KEY,
    tenant          TEXT NOT NULL,
    key_hash        TEXT NOT NULL UNIQUE,
    allowed_origins JSONB NOT NULL,
    rate_per_minute INT NOT NULL DEFAULT 30,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at      TIMESTAMPTZ
);

-- Corporate accounts (monthly-billed business travel).
CREATE TABLE IF NOT EXISTS corporate_accounts (
    account_id      TEXT PRIMARY KEY,
    tenant          TEXT NOT NULL,
    name            TEXT NOT NULL,
    currency        TEXT NOT NULL CHECK (currency IN ('EUR', 'NGN')),
    po_required     BOOLEAN NOT NULL DEFAULT TRUE,
    approval_over_minor BIGINT,                 -- NULL = no approval gate
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS corporate_travelers (
    traveler_id     TEXT PRIMARY KEY,
    account_id      TEXT NOT NULL REFERENCES corporate_accounts(account_id),
    name            TEXT NOT NULL,
    email           TEXT NOT NULL,
    default_cost_center TEXT,
    max_service_class TEXT NOT NULL DEFAULT 'executive',
    self_book       BOOLEAN NOT NULL DEFAULT TRUE
);

-- Flight snapshots: append-only observation log (provenance is a column,
-- never an assumption). The adjustment decisions themselves flow through
-- event_outbox as trip.rebooked / flight.status_changed.
CREATE TABLE IF NOT EXISTS flight_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    tenant          TEXT NOT NULL,
    flight          TEXT NOT NULL,
    status          TEXT NOT NULL,
    scheduled_arrival TIMESTAMPTZ NOT NULL,
    estimated_arrival TIMESTAMPTZ,
    observed_at     TIMESTAMPTZ NOT NULL,
    provenance      TEXT NOT NULL CHECK (provenance IN ('feed', 'manual', 'none')),
    terminal        TEXT,
    UNIQUE (tenant, flight, observed_at)
);
