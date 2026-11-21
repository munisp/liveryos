# ADR 0001 — PostgreSQL as the platform database; MariaDB fenced to ERPNext only

Date: 2026-11-21 · Status: accepted · Decided by: platform owner directive ("use postgres instead of mariadb")

## Context

The Phase 1 stack put MariaDB at the center because Frappe/ERPNext's default
(and only production-tested) database adapter is MariaDB. The owner has
directed PostgreSQL as the platform database.

The hard upstream fact: **ERPNext v15 does not run on PostgreSQL.** The
Frappe framework ships an experimental Postgres adapter, but ERPNext's
query surface (MariaDB-specific SQL, full-text indexes, DDL assumptions)
is not tested against it. Running the bench on Postgres would mean owning
an upstream fork — exactly the kind of maintenance trap the report
(Ch. 4) warns against.

## Decision

Split the data plane by ownership:

- **PostgreSQL (`livery-db`) is the platform database for everything
  LiveryOS owns**: event outbox, position store, tenant registry, GA
  import staging, affiliate engine, audit log — plus the state stores of
  the satellites that support it (Keycloak realms, Traccar positions),
  replacing their default embedded H2 dev stores.
- **MariaDB remains ONLY as ERPNext's embedded substrate** — it holds the
  DocType schema ERPNext manages. No LiveryOS-owned data is written there;
  the bench reaches LiveryOS data through `LIVERY_PG_DSN`.
- **Metabase keeps its own Postgres container** (boundary-service
  isolation, AGPL) and reports off a *read replica* — unchanged.
- **Nominatim's internal Postgres** is its own (geodata import tooling) —
  unchanged; it is not a general-purpose store we write to.

If Frappe's Postgres support matures to ERPNext-tested status, migrating
the bench off MariaDB is a future option; the fence makes that a contained
change.

## Consequences

- One Postgres instance (`livery-db`), database-per-concern
  (`livery_core`, `keycloak`, `traccar`), provisioned by
  `infra/postgres-init.sql`. The `livery_core` baseline schema (tenant
  registry, event outbox, position fixes, GA import staging, affiliate
  partners) ships in the same file.
- Dual-DB operational reality: backups, PITR, and upgrades cover both
  engines. Accepted — the ERPNext side is upstream-managed schema only.
- The Event Outbox and Redis-free position store now have a concrete
  home; `booking_events._emit` graduates from log-only to real outbox
  writes in the bench-integration step.
