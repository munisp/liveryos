# LiveryOS

Multi-tenant livery/chauffeur operations platform — ERPNext back office + custom Frappe operational core + permissive OSS satellites. Dual-market by design: **Portugal (EU)** and **Nigeria**.

Built per the GroundAlliance re-implementation report (`groundalliance.agent.final.md`, 2026-11-21): re-implement the GroundAlliance feature surface with ERPNext + open source, and integrate with GroundAlliance strictly as a transition bridge.

## Architecture (report Ch. 5)

```
[Passenger PWA] [Driver Flutter app] [Dispatch console]
        |                |                    |
        v                v                    v
   Frappe bench (site-per-tenant)  <—— system of record
   ├── ERPNext: CRM → Quotation → Sales Order → Sales Invoice → Payment Entry
   ├── livery_core (this app): rate engine, dispatch state, affiliate engine
   └── event catalog: booking.created / trip.assigned / trip.status_changed / invoice.paid
        |
        +-- Valhalla (routing, MIT)  +-- Nominatim (geocoding, boundary svc)
        +-- Traccar (GPS, Apache-2.0) → webhook → Centrifugo (realtime, MIT)
        +-- Keycloak (SSO, realm/tenant)  +-- Metabase (BI, AGPL, read-replica only)
```

- **Tenancy**: site-per-tenant (per-tenant DB, backup, isolation) — `scripts/new-tenant.sh`.
- **Databases**: **PostgreSQL is the platform DB** (`livery-db`): event outbox, position store, tenant registry, GA import staging, affiliate engine, Keycloak + Traccar state. MariaDB is fenced to ERPNext's own DocType schema only (upstream hard requirement — see `docs/adr/0001-postgres-platform-db.md`). Schema baseline: `infra/postgres-init.sql`.
- **Money**: integer minor units everywhere; EUR and NGN never blended.
- **License posture**: ERPNext is GPL-3.0 (SaaS use, not distribution — counsel flag open, report Ch. 9); Frappe framework MIT; satellites permissive-only with two documented boundary services (Metabase AGPL, Nominatim GPLv2/v3+).

## Status

| Phase | Content | State |
|-------|---------|-------|
| Phase 0 | Vendor-diligence pack (`docs/phase0/`): GA email script, export-completeness checklist, contract review, Gate 1 scorecard, GNet application prep | ✅ done |
| Phase 1 | Foundation scaffold: full-stack compose, tenant provisioning, `livery_core` app skeleton, **rate engine (implemented + tested)**, ride dispatch state machine | ✅ this commit |
| Phase 2 | Dispatch board assignment engine, position ingest (Traccar→Centrifugo), event catalog, chauffeur compliance sweep, GA transition adapter (CSV live / API gated on Gate 1) | ✅ done — 39 tests |
| Phase 3 | **Affiliate engine** (farm-in/out, splits, blocking lists), **GRES mapping** (lossless round-trip primitive), **passenger + driver PWAs** (offline-first), shared API contract | ✅ done — 58 tests |
| Phase 4 | Migration pipeline (idempotent delta imports), nightly reconciliation + cutover gate, event outbox dispatcher, cutover runbook | ✅ done — 72 tests |

## The rate engine

`apps/livery_core/livery_core/rate_engine/engine.py` — the critical-path component (report Ch. 3 §3.2.1). Multiplier/fixed/percentage rules keyed on zone polygon, service class, time-of-day, date, weekday, and lead time; priority ordering with stackable/non-stackable termination; audited manual overrides; `parity_replay()` harness implementing the Gate 3 acceptance test (≥95% within tolerance incl. explained variances; ≥98% exact component target). 12 unit tests cover the full rule surface.

## Development

```bash
python -m pytest tests/ -q                              # domain tests, no bench needed
docker compose -f infra/docker-compose.yml config -q    # validate stack
bash scripts/new-tenant.sh acme PT ops@acme.example     # provision a tenant site
```

## Phase 2 modules

- `dispatch/board.py` — assignment engine: hard filters (vehicle class, compliance, idle) → haversine proximity ranking; `suggest_assignments` / `auto_assign` / board `tick()`; ETAs are always labeled `eta_source="estimate"` (straight-line) until Valhalla-routed ETAs land with bench integration.
- `adapters/position_ingest.py` — Traccar webhook → `PositionStore` (stale-fix drop, bounded history) → Centrifugo `tenant:{slug}#positions`; hourly `poll_traccar_fallback` sweep covers missed webhooks; duplicate/out-of-order fixes never reach the boards.
- `adapters/events.py` + `booking_events.py` + `billing_events.py` — canonical event catalog from Sales Order / Invoice / Payment Entry hooks; log-only emit until the outbox DocType lands (honest degrade).
- `adapters/compliance.py` — daily licence/medical expiry sweep (gap item 12); non-compliant chauffeurs drop out of board eligibility and trigger alerts. PT (TVDE) and NG (LASG/LASTMA) aware.
- `adapters/transition/ga_adapter.py` — GA anti-corruption seam: CSV export path live (per-row quarantine, Decimal minor-unit money, US+ISO datetimes, status mapping, `provenance="ga_import"`); `ApiSource` raises `NotConfigured` until Gate 1 delivers documented API + sandbox.

## Phase 3 modules

- `affiliates/engine.py` — the moat component (report Ch. 3 §3.2.6): eligibility with readable rejection reasons (direction, active, blocking lists over airports/zones/service classes), dispatcher-preference-sovereign selection, and Decimal minor-unit settlement splits where partner + operator ALWAYS sum to the gross (no penny lost, either currency). Per-booking-quote partners settle honestly `None`, never a fabricated zero.
- `affiliates/gres.py` — GRES draft mapping: canonical ↔ wire payload, bidirectional status codes, and `roundtrip_check()` — the conformance primitive. When GNet's sandbox schema lands, only `_FIELD_MAP` changes; the round-trip property and its tests stay.
- `api/contract.py` — the request/response shapes both apps speak: passenger booking view (ETA always source-labeled), driver transitions enforced server-side against the ride state machine, position reports validated (range, future-timestamp drop, **naive timestamps rejected** — coercing device-local time to UTC would fabricate fix times).
- `apps/passenger_pwa/` — offline-first tracking PWA: cache-first shell, booking view cached in localStorage, honest "offline — showing last known state" indicator with timestamp, Centrifugo live updates when reachable, WhatsApp-first share (no install, no account needed by recipient), `?ref=` deep links.
- `apps/driver_pwa/` — status transitions (client hints, server enforces), GPS reporting every 15s with ISO-8601 offset-carrying timestamps, localStorage offline queue with in-order flush and visible queued count — a driver always knows exactly what hasn't reached dispatch.

## Phase 4 modules

- `migration/pipeline.py` — GA CSV import runner: checksummed batches (provenance anchor), idempotent re-import via content hashes (unchanged rows skip), delta detection sizing the cutover window, quarantine flowing into `ga_import_rows` staging.
- `migration/reconcile.py` — nightly parallel-run reconciliation: per-booking match by external_id, classification (`missing_in_livery` / `missing_in_ga` — explained native bookings / exact `money_mismatch` / `status_mismatch`), and `cutover_gate()` — 7 consecutive clean nights as the go/no-go, with blocking evidence attached.
- `adapters/outbox.py` — event outbox dispatcher: at-least-once delivery (subscribers dedupe on `event_id`), exponential backoff capped at 300s, dead-letter after 8 attempts (an operator queue, never silent loss), HMAC-signed webhook delivery. Completes the Phase 2 emit seam.
- `docs/phase4/cutover-runbook.md` — the executable T-90 → T+30 runbook: every step names its artifact and pass criterion, with STOP conditions and four rollback triggers.

CI: `.github/workflows/ci.yml` — pytest + compose/JSON/shell validation + PWA JS syntax/manifest checks.
