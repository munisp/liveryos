# Phase 4 — Tenant Cutover Runbook (GA → LiveryOS)

Executable playbook for migrating one tenant off GroundAlliance (report Ch. 8).
Every step names its artifact and its pass criterion. If a criterion fails, the
runbook says STOP — a cutover that ignores its own gates is how operators end
up running two systems forever.

Timing anchor: all dates derive from the **GA notice deadline** (contract-review
checklist row 1), NOT the renewal date:
`renewal − notice window − 30–90d read-only − 2–4wk parallel = latest build-complete date`.

## T-90 → T-60 — Evidence (Gate 1 closes)

- [ ] GA diligence email answered in writing (docs/phase0/ga-diligence-email.md, all 4 demands)
- [ ] Export completeness scored (10-entity checklist; **STOP if FAIL on rows 1/3/5 → migration-only with manual rekey budget**)
- [ ] Contract reviewed (11 clauses); notice date diaried in two places
- [ ] Gate 1 scorecard signed (≥15 integration-first / 9–14 migration-leaning / ≤8 migration-only)
- [ ] First full CSV export imported: `stage_import()` → `ga_import_rows`; batch checksum filed

## T-60 → T-30 — Build + parallel prep

- [ ] Tenant site provisioned: `scripts/new-tenant.sh <slug> <PT|NG> <email>`; registry row in `tenant_registry`
- [ ] Rate cards re-keyed into the rate engine; **parity replay ≥95% gate-rate / ≥98% component on 500 historical quotes** (Gate 3 harness — `parity_replay()`). STOP if below threshold: fix rules, replay again. This is the single highest-leverage gate.
- [ ] Drivers/vehicles imported; compliance expiry dates machine-readable (checklist row 7)
- [ ] Corporate account hierarchies + PO numbers verified row-by-row against export
- [ ] Payment re-capture campaign started (Ch. 8 §8.3.1): card tokens NEVER export — customers get a "update your card" link (Stripe PT / Paystack NG); target ≥70% re-captured before cutover
- [ ] Affiliate partners called (not emailed): continuity confirmed per partner; terms re-keyed into `affiliate_partners`

## T-30 → T-14 — Parallel run

- [ ] Both systems live; ALL new bookings entered in both (double-entry discipline, 7–14 days)
- [ ] Nightly reconciliation: `reconcile_bookings()` — every `missing_in_livery`, `money_mismatch`, `status_mismatch` investigated same morning
- [ ] Delta velocity measured: `delta_summary()` across weekly imports → sizes the final delta-sync window
- [ ] Staff training done per role (dispatch, AR, drivers on the driver PWA)

## T-14 → T-1 — Cutover gate

- [ ] **Go/no-go: `cutover_gate()` — 7 consecutive clean nights required.** Any blocking night resets the streak. No streak, no cutover — extend parallel run.
- [ ] Final full GA export taken and frozen (immutable, checksummed)
- [ ] Rollback plan printed: criteria + who decides + how GA access is re-enabled (keep GA credentials live until T+30)
- [ ] Cutover window booked in the lowest-velocity slot from `delta_summary()` (typically Sun 02:00–06:00 local)

## T-0 — Cutover day

- [ ] GA set read-only (or bookings frozen by policy if read-only unavailable)
- [ ] Final delta import: `stage_import()` with `seen_hashes` from the last batch — only new/changed rows staged
- [ ] Final reconciliation run: **clean verdict required before opening LiveryOS to dispatch**
- [ ] Dispatch switched: board goes live on LiveryOS; GA kept open read-only on a second screen
- [ ] First 10 live rides watched end-to-end (book → assign → track → complete → invoice → payment)

## T+1 → T+30 — Stabilize + exit

- [ ] Daily reconciliation continues vs. GA read-only data (drift should be zero — LiveryOS is now the only writer)
- [ ] Payment re-capture closes ≥85%; stragglers moved to pay-by-link
- [ ] GA non-renewal notice confirmed received IN WRITING (if not sent at Gate 1)
- [ ] GA data-return obligation exercised; certified deletion requested for AFTER the read-only window (contract checklist row 4)
- [ ] GA subscription cancelled; credentials revoked T+30 — never before
- [ ] Post-mortem: quarantined rows cleared, reconciliation log archived, lessons into this runbook

## Rollback triggers (any one → execute rollback, not debate)

1. Reconciliation shows ≥2% of a day's bookings missing in LiveryOS at T+1..T+7
2. Rate engine quotes diverge >5% from GA on ≥10% of sampled bookings in the first 48h
3. Payment capture failure rate >20% in the first 48h
4. Dispatch board cannot place rides for >30 min during operating hours

Rollback = reopen GA write access, freeze LiveryOS bookings, dual-enter until root cause is fixed, re-run the cutover gate from zero. A rollback is a cost; a botched cutover is a tenant lost.
