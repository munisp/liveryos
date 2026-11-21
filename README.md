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
- **Money**: integer minor units everywhere; EUR and NGN never blended.
- **License posture**: ERPNext is GPL-3.0 (SaaS use, not distribution — counsel flag open, report Ch. 9); Frappe framework MIT; satellites permissive-only with two documented boundary services (Metabase AGPL, Nominatim GPLv2/v3+).

## Status

| Phase | Content | State |
|-------|---------|-------|
| Phase 0 | Vendor-diligence pack (`docs/phase0/`): GA email script, export-completeness checklist, contract review, Gate 1 scorecard, GNet application prep | ✅ done |
| Phase 1 | Foundation scaffold: full-stack compose, tenant provisioning, `livery_core` app skeleton, **rate engine (implemented + tested)**, ride dispatch state machine | ✅ this commit |
| Phase 2 | Dispatch board assignment engine, position ingest (Traccar→Centrifugo), event catalog, chauffeur compliance sweep, GA transition adapter (CSV live / API gated on Gate 1) | ✅ done — 39 tests |
| Phase 3 | Driver/passenger apps, affiliate engine, GNet GRES conformance | planned |
| Phase 4 | Hardening + first tenant migration (report Ch. 8 playbook) | planned |

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

CI: `.github/workflows/ci.yml` — pytest + compose/JSON/shell validation.
