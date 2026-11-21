"""Migration pipeline tests — batch provenance, idempotency, delta (Ch. 8)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "livery_core"))

from livery_core.migration.pipeline import (  # noqa: E402
    booking_content_hash, delta_summary, rows_checksum, stage_import,
)

ROWS = [
    {"Res No": "GA-1001", "Account": "Hotel Avenida", "Passenger Name": "Maria Silva",
     "Pickup Date": "11/25/2026 09:30", "Pickup": "LIS Airport", "To": "Rua Augusta 24",
     "Vehicle Type": "Sedan", "Status": "Confirmed", "Total": "€145.50", "Ccy": "EUR"},
    {"Res No": "GA-1002", "Account": "Tivoli", "Passenger Name": "Joao Costa",
     "Pickup Date": "11/26/2026 14:00", "Pickup": "Rossio", "To": "Sintra",
     "Vehicle Type": "Sedan", "Status": "Confirmed", "Total": "€80.00", "Ccy": "EUR"},
]


def test_first_import_all_new():
    batch, records = stage_import(ROWS, "acme", "ga-export-w1.csv", seen_hashes={})
    assert batch.row_count == 2 and batch.new == 2 and batch.imported == 2
    assert batch.checksum == rows_checksum(ROWS)
    assert all(r.status == "imported" and r.content_hash for r in records)
    assert len({r.batch_id for r in records}) == 1


def test_reimport_is_idempotent_and_detects_changes():
    batch1, recs1 = stage_import(ROWS, "acme", "w1.csv", seen_hashes={})
    seen = {r.external_id: r.content_hash for r in recs1}

    # same export again → everything unchanged, nothing staged
    batch2, recs2 = stage_import(ROWS, "acme", "w1-again.csv", seen_hashes=seen)
    assert batch2.unchanged == 2 and batch2.imported == 0 and recs2 == []

    # one row changed (price), one new row
    changed = [dict(r) for r in ROWS]
    changed[0]["Total"] = "€150.00"
    changed.append({**ROWS[1], "Res No": "GA-1003"})
    batch3, recs3 = stage_import(changed, "acme", "w2.csv", seen_hashes=seen)
    assert batch3.changed == 1 and batch3.new == 1 and batch3.unchanged == 1
    staged_ids = {r.external_id for r in recs3}
    assert staged_ids == {"GA-1001", "GA-1003"}
    # and the content hash actually changed for GA-1001
    old = seen["GA-1001"]
    new = [r for r in recs3 if r.external_id == "GA-1001"][0].content_hash
    assert old != new


def test_quarantine_flows_to_staging():
    rows = ROWS + [{"Res No": "GA-BAD", "Status": "Bogus", "Pickup Date": "11/26/2026 14:00"}]
    batch, records = stage_import(rows, "acme", "w3.csv", seen_hashes={})
    assert batch.quarantined == 1
    q = [r for r in records if r.status == "quarantined"][0]
    assert q.external_id == "GA-BAD" and "unmapped GA status" in q.error
    assert q.row_no == 4  # header=1 → data starts at 2


def test_delta_summary_sizes_cutover_window():
    b1, _ = stage_import(ROWS, "acme", "w1.csv", seen_hashes={})
    b2, _ = stage_import(ROWS, "acme", "w1b.csv", seen_hashes={})
    s = delta_summary([b1, b2])
    assert s["batches"] == 2 and s["total_rows"] == 4 and s["total_new"] == 4
    assert delta_summary([]) == {"batches": 0}
