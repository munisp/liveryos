# Phase 0 — Export Completeness Test Checklist

Run against the tenant's live GA account during Gate 1 (weeks 2–3). Every row gets one of: **PASS** (full data, machine-readable) / **PARTIAL** (fields missing — list them) / **FAIL** (unavailable or manual-only). Record format, row counts, and a checksum per export. These counts become the baseline for the nightly reconciliation during parallel run (report Ch. 8).

| # | Entity | What to request/export | Minimum pass criteria | Fields that commonly go missing |
|---|--------|------------------------|-----------------------|--------------------------------|
| 1 | Customers | Full customer list w/ contact details | CSV/API; all rows; emails + phones intact | Phone normalization, tags/notes |
| 2 | Passengers | Passenger profiles distinct from bookers | Linked to customer IDs | Preferences, notes |
| 3 | Reservations (future) | All future-dated bookings | Full field set incl. status, pickup, routing, price, source | Status history, internal notes |
| 4 | Reservations (historical) | 24 months back minimum | Same schema as #3; timestamps preserved | Audit trail, change log |
| 5 | Rate cards / pricing rules | Every zone, class, time rule | Machine-readable; rule parameters not prose | Surge calendars, override history |
| 6 | Corporate accounts | Hierarchies + PO numbers + billing contacts | Parent/child links intact | PO numbers, credit terms |
| 7 | Drivers & vehicles | Roster incl. licence/medical expiry | Expiry DATES machine-readable, not document scans | Compliance documents (accept metadata-only) |
| 8 | Open AR | Outstanding balances per account | Per-invoice breakdown, aging | Partial payment history |
| 9 | Affiliate/farm-out terms | Partner list + split % + blocking lists | Written or exportable per-partner terms | Split rules (often only in email threads) |
| 10 | Payment references | Card-on-file presence per customer | Count + which PSP holds the tokens | **Tokens themselves NEVER export** — plan re-capture campaign (report Ch. 8 §8.3.1) |

## Scoring

- **9–10 PASS** → export viable; Gate 1 criterion "export completeness" = met.
- **6–8 PASS, no FAIL on rows 1/3/5** → viable with compensating manual steps; note cost.
- **Any FAIL on rows 1, 3, or 5** → export NOT viable → Gate 2 forces migration-only path (report Ch. 7 §7.4.5).
- **Any PARTIAL** → log the missing fields; each becomes a manual-rekey estimate line in the migration budget.

## Method notes

- Prefer API if provided; else scheduled CSV export; else document exact manual steps with screenshots (this is the evidence file if the vendor later disputes what was available).
- Run exports twice, 7 days apart. Diff the row counts — delta tells you the live booking velocity, which sizes the final delta-sync window at cutover.
- Keep raw exports immutable (read-only folder). All transformation happens on copies.
