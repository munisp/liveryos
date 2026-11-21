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
| Phase 5 | VPS deployment pack: provisioning guide, Caddy TLS proxy + prod overlay, env template, idempotent bootstrap, nightly backup script | ✅ done |
| Phase 6 | **GA-parity gap closure (6 workstreams)**: payments breadth (Stripe EUR + Paystack NGN, pre-auth/capture, EBizCharge-parity posting + commission automation), telephony & messaging (LiveKit masked calls, Pipecat voice bots, WhatsApp/SMS/voice notifications), flight tracking (auto-adjust with hysteresis + lock window), booking widget + corporate portal (PO policy, budgets, approvals), Metabase BI (7 views + 2 dashboards), native Flutter apps | ✅ done — 147 tests |
| Phase 7 | **LongCat voice concierge**: LongCat-AudioDiT (MIT, Meituan) as concierge TTS — engine selection with per-utterance provenance (longcat → labeled piper fallback → none), voice cloning with HARD consent gating (GDPR Art.9 / NDPA), verbatim number rendering (ETA source words are spoken), GPU-gated serving service (no CUDA → no service, piper instead — never stalled CPU calls) | ✅ done — 173 tests |

## Phase 7 modules (LongCat voice concierge)

- `adapters/voice/longcat.py` — synthesis config + **honest engine selection**: configured+healthy → `longcat-1b/3.5b`; unreachable/unconfigured → `piper-fallback` (LABELED); nothing → `none` (caller renders text, no fake audio). Duration estimation is a declared heuristic, not a promise.
- `adapters/voice/profiles.py` — zero-shot voice cloning with **consent as a hard precondition**: cloned profiles refuse to register without a consent artifact + scope + prompt transcript; scope checks per tenant/purpose; revocation tombstones AND dereferences the prompt audio. The default concierge voice is fully synthetic — always lawful.
- `adapters/voice/concierge.py` — personas (pt-PT / en-NG) with mandatory bot disclosure; quotes/statuses rendered from templates with **verbatim fields** (the ETA source word is SPOKEN — "about 12 minutes, estimate"); after-hours escalation never promises a human who isn't rostered.
- `services/longcat_tts_server.py` — HTTP wrapper over upstream inference (24 kHz WAV); **exits 3 without CUDA** rather than serving stalled CPU audio; `/health` gates the domain layer's engine selection. Compose service `longcat-tts` behind the `gpu` profile (GPU-less hosts simply omit it).

## Phase 6 modules (GA-parity workstreams)

- `adapters/payments/` — gateway protocol with **currency fencing** (EUR→Stripe/SEPA, NGN→Paystack; a mismatch raises, we never auto-convert); pre-auth/capture-on-completion intent lifecycle (capture ≤ authorization, idempotency keys, replay-safe store); **EBizCharge parity**: captured payments post to invoices as `invoice.paid` events, and farmed-trip captures auto-emit affiliate commission settlements via `split_minor` (zero penny loss; per-booking-quote partners settle honestly as `commission_pending_quote`).
- `adapters/telephony/` — **masked calling** (GA $30/mo parity): proxy-number pool with one-active-assignment-per-number, expiry, no silent un-masking (pool exhausted → "calling unavailable"); **LiveKit** access tokens (real HS256 JWTs, stdlib-only); **Pipecat** voice-bot pipeline specs (bot discloses itself, emits structured intents only, never fakes a human after hours); notification composer (NG = WhatsApp-first, PT = SMS-first, critical events add voice drop) — composed requests are *queued* to the outbox, never claimed sent.
- `adapters/flights/` — AeroAPI-class feed parsing (unknown statuses quarantine to `UNKNOWN`, naive datetimes rejected, delay-without-estimate downgrades) + pickup adjustment policy: hysteresis (small wobbles don't move pickups), lock window (last 45 min belong to a human), cancellations propose rebooking and NEVER silently cancel the ride.
- `widget/` + `apps/booking_widget/` + `apps/corporate_portal/` — embeddable widget (origin-bound keys, sha256 storage, token-bucket rate limits) and corporate tool (PO-required enforcement, service-class caps, cost-center budgets, approval routing, currency match — rejected bookings carry readable reasons and there is no "book anyway").
- `bi/` — 7 read-only SQL views on livery-db (revenue grouped by currency, NEVER blended) + 2 Metabase dashboard manifests (Operations, Finance, all tenant-scoped) + idempotent provisioner script.
- `apps/driver_flutter/` + `apps/passenger_flutter/` — native apps (ride queue with offline transition journal, 15s position reporting with device timestamps, LiveKit masked calls, WhatsApp share). No Flutter SDK in CI — structurally validated, honestly labeled as such.

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

## Deployment (Phase 5)

`docs/deployment/vps-provisioning.md` is the full guide: sizing per market (8vCPU/16GB start; 32GB before importing the second Nominatim country; Lagos ground-truth sprint before selling ETAs), OS hardening, DNS + wildcard TLS via Caddy, first-boot order (Nominatim import takes hours — tenants after, not during), first tenant, backups, monitoring floor, and an honest list of what only a live boot can verify. Deploy: `cp infra/.env.example infra/.env` (fill every CHANGEME), then `bash scripts/deploy-vps.sh`. Production overlay `infra/docker-compose.prod.yml` adds Caddy and removes all dev port exposure — only 80/443 face the internet. Nightly: `scripts/backup.sh` (pg_dump ×3 + mariadb-dump + sites volume + manifest + optional rclone offsite + retention).

## Phase 4 modules

- `migration/pipeline.py` — GA CSV import runner: checksummed batches (provenance anchor), idempotent re-import via content hashes (unchanged rows skip), delta detection sizing the cutover window, quarantine flowing into `ga_import_rows` staging.
- `migration/reconcile.py` — nightly parallel-run reconciliation: per-booking match by external_id, classification (`missing_in_livery` / `missing_in_ga` — explained native bookings / exact `money_mismatch` / `status_mismatch`), and `cutover_gate()` — 7 consecutive clean nights as the go/no-go, with blocking evidence attached.
- `adapters/outbox.py` — event outbox dispatcher: at-least-once delivery (subscribers dedupe on `event_id`), exponential backoff capped at 300s, dead-letter after 8 attempts (an operator queue, never silent loss), HMAC-signed webhook delivery. Completes the Phase 2 emit seam.
- `docs/phase4/cutover-runbook.md` — the executable T-90 → T+30 runbook: every step names its artifact and pass criterion, with STOP conditions and four rollback triggers.

CI: `.github/workflows/ci.yml` — pytest + compose/JSON/shell/dashboard-manifest validation + PWA/widget/portal JS syntax and manifest checks.
