# Gate 1 Scorecard — Go/No-Go for Integration Depth

Score at the end of Phase 0 (≈4 weeks). Every score must cite written evidence (email, export file, contract clause) — no verbal assurances.

## Scoring

| Dimension | Weight | 2 = Good | 1 = Workable | 0 = Hostile/Absent |
|-----------|--------|----------|--------------|--------------------|
| API documented + sandbox delivered | 3 | Docs + working sandbox | Docs only, no sandbox | No docs / marketing-only claims |
| Export completeness (checklist score) | 3 | 9–10 PASS | 6–8 PASS, core rows OK | FAIL on customers/reservations/rates |
| Contract exit posture | 2 | Convenience exit, clean data return | Notice window manageable, data return adequate | No exit, hostile data terms |
| GroundXchange/Deem as machine seam | 1 | Two-way, documented | One-way or partial | Add-on is marketing-only |
| Vendor responsiveness | 1 | Complete written answers ≤10 days | Partial or slow | Evasive/silent |

**Total: 0–20.**

## Decision

- **15–20 → Integration-first transition.** Build the GA adapter (anti-corruption layer), run shadow sync, plan strangler-fig cutover. Proceed to Gate 2 with integration-depth options open.
- **9–14 → Migration-leaning.** Shadow sync on CSV-export cadence only; shorter coexistence; cutover earlier with tighter reconciliation.
- **0–8 → Migration-only.** No adapter investment. All effort goes to export scripts + parallel run + cutover runbook (report Ch. 8). Notify GA of non-renewal at the earliest contractual window.

## Hard gates (override the score)

- No exportable **reservations** (checklist row 3/4 FAIL) → migration-only regardless of total.
- Contract clause restricting competing system (checklist row 10) → STOP. Counsel before anything else.
- No DPA for an EU (Portugal) tenant → compliance flag independent of migration; handle in parallel.

## Sign-off

Gate 1 pass requires: completed scorecard + evidence folder + signed recommendation (build lead + tenant decision-maker). File under `evidence/tenant-{slug}/gate1/`.
