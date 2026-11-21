"""Migration import pipeline (report Ch. 8 §8.2).

GA CSV export rows → staging (ga_import_rows) → canonical bookings, as a
repeatable, auditable, idempotent runner:

- Every run is a BATCH: id, tenant, source name, row count, and a SHA-256
  checksum of the raw rows, so "which export did this come from" is never
  a guess during parallel run.
- Re-importing the same export is safe: unchanged reservations are
  detected by content hash and skipped; only new/changed rows produce
  staging inserts. The cutover delta-sync window is sized by exactly this
  delta rate (export-completeness-checklist method note).
- Quarantine from the GA adapter flows through unchanged: bad rows land
  in staging with status='quarantined' + error, never silently dropped
  and never blocking the batch.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional
from uuid import uuid4

from ..adapters.transition.ga_adapter import CanonicalBooking, CsvExportSource


@dataclass(frozen=True)
class StagingRecord:
    """One ga_import_rows row (infra/postgres-init.sql)."""
    batch_id: str
    tenant: str
    row_no: int
    raw: dict
    external_id: Optional[str]
    status: str                       # imported | quarantined
    error: Optional[str]
    canonical: Optional[CanonicalBooking]
    content_hash: Optional[str]


@dataclass
class ImportBatch:
    tenant: str
    source_name: str
    batch_id: str = field(default_factory=lambda: uuid4().hex)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    row_count: int = 0
    checksum: str = ""
    imported: int = 0
    quarantined: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0


def booking_content_hash(b: CanonicalBooking) -> str:
    """Stable hash of the fields that matter for change detection."""
    payload = json.dumps({
        "customer": b.customer, "passenger": b.passenger,
        "pickup_at": b.pickup_at.isoformat(), "pickup": b.pickup_address,
        "dropoff": b.dropoff_address, "class": b.service_class,
        "status": b.status, "quoted_minor": b.quoted_minor, "ccy": b.currency,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def rows_checksum(rows: list[dict]) -> str:
    """SHA-256 over the raw export rows (sorted for determinism) — the
    batch provenance anchor."""
    payload = json.dumps(rows, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def stage_import(rows: Iterable[dict], tenant: str, source_name: str,
                 seen_hashes: Optional[dict[str, str]] = None
                 ) -> tuple[ImportBatch, list[StagingRecord]]:
    """Run one import batch. `seen_hashes` maps external_id → content_hash
    from prior imports (the idempotency/delta mechanism); pass {} for a
    first import. Returns (batch summary, staging records to insert)."""
    rows = list(rows)
    batch = ImportBatch(tenant=tenant, source_name=source_name,
                        row_count=len(rows), checksum=rows_checksum(rows))
    seen = dict(seen_hashes or {})
    result = CsvExportSource().import_rows(rows)
    records: list[StagingRecord] = []

    for b in result.bookings:
        h = booking_content_hash(b)
        prior = seen.get(b.external_id)
        if prior is None:
            batch.new += 1
        elif prior != h:
            batch.changed += 1
        else:
            batch.unchanged += 1
            continue                      # idempotent: unchanged rows skipped
        records.append(StagingRecord(
            batch_id=batch.batch_id, tenant=tenant, row_no=0, raw={},
            external_id=b.external_id, status="imported", error=None,
            canonical=b, content_hash=h))
        batch.imported += 1

    for row_no, raw, error in result.quarantined:
        records.append(StagingRecord(
            batch_id=batch.batch_id, tenant=tenant, row_no=row_no, raw=raw,
            external_id=str(raw.get("Res No") or raw.get("reservation") or "") or None,
            status="quarantined", error=error, canonical=None, content_hash=None))
        batch.quarantined += 1

    return batch, records


def delta_summary(batches: list[ImportBatch]) -> dict:
    """Cutover sizing input: change velocity across parallel-run batches.
    The final delta-sync window must comfortably cover the p95 booking-
    change rate between the last full export and cutover (Ch. 8 §8.4)."""
    if not batches:
        return {"batches": 0}
    return {
        "batches": len(batches),
        "total_rows": sum(b.row_count for b in batches),
        "total_new": sum(b.new for b in batches),
        "total_changed": sum(b.changed for b in batches),
        "total_quarantined": sum(b.quarantined for b in batches),
        "avg_delta_per_batch": round(
            sum(b.new + b.changed for b in batches) / len(batches), 1),
    }
