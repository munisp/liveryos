"""Parallel-run reconciliation (report Ch. 8 §8.4).

Every night of the parallel run, the GA side and the LiveryOS side are
compared booking-by-booking. The cutover go/no-go gate is N consecutive
clean nights (default 7) — this module produces the verdict, with every
discrepancy classified so the morning standup talks about causes, not
counts.

Classification:
- missing_in_livery : in GA, not in LiveryOS (ingest gap — investigate)
- missing_in_ga     : in LiveryOS, not in GA (native booking — EXPECTED
                      once direct sales channel opens; classified, not failed)
- money_mismatch    : quoted_minor differs (exact match by default — money
                      truth is not a tolerance game)
- status_mismatch   : mapped statuses differ (GA status already mapped to
                      Livery status by the adapter, so this is apples-to-apples)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from ..adapters.transition.ga_adapter import CanonicalBooking


@dataclass(frozen=True)
class Discrepancy:
    kind: str                         # missing_in_livery | missing_in_ga | money_mismatch | status_mismatch
    external_id: str
    detail: str
    ga_value: Optional[str] = None
    livery_value: Optional[str] = None


@dataclass
class ReconReport:
    run_date: date
    tenant: str
    ga_total: int = 0
    livery_total: int = 0
    matched: int = 0
    discrepancies: list[Discrepancy] = field(default_factory=list)

    def by_kind(self, kind: str) -> list[Discrepancy]:
        return [d for d in self.discrepancies if d.kind == kind]

    @property
    def clean(self) -> bool:
        """Clean = no unexplained drift. missing_in_ga is explained
        (native bookings) and does not fail the night."""
        return not (self.by_kind("missing_in_livery")
                    or self.by_kind("money_mismatch")
                    or self.by_kind("status_mismatch"))


def reconcile_bookings(ga_side: list[CanonicalBooking],
                       livery_side: list[CanonicalBooking],
                       tenant: str, run_date: date,
                       money_tolerance_minor: int = 0) -> ReconReport:
    ga = {b.external_id: b for b in ga_side}
    lv = {b.external_id: b for b in livery_side}
    rep = ReconReport(run_date=run_date, tenant=tenant,
                      ga_total=len(ga_side), livery_total=len(livery_side))

    for ext_id, g in ga.items():
        l = lv.get(ext_id)
        if l is None:
            rep.discrepancies.append(Discrepancy(
                "missing_in_livery", ext_id,
                f"GA booking {ext_id} ({g.passenger}, {g.pickup_at.date()}) not in LiveryOS"))
            continue
        rep.matched += 1
        if g.quoted_minor is not None and l.quoted_minor is not None:
            diff = abs(g.quoted_minor - l.quoted_minor)
            if diff > money_tolerance_minor:
                rep.discrepancies.append(Discrepancy(
                    "money_mismatch", ext_id,
                    f"quoted differs by {diff} minor ({g.currency})",
                    str(g.quoted_minor), str(l.quoted_minor)))
        if g.status != l.status:
            rep.discrepancies.append(Discrepancy(
                "status_mismatch", ext_id,
                "status differs after GA→Livery mapping",
                g.status, l.status))

    for ext_id, l in lv.items():
        if ext_id not in ga:
            rep.discrepancies.append(Discrepancy(
                "missing_in_ga", ext_id,
                f"LiveryOS-native booking {ext_id} — expected once direct channel opens"))
    return rep


def cutover_gate(reports: list[ReconReport], required_clean_nights: int = 7) -> dict:
    """Go/no-go: the LAST required_clean_nights reports must all be clean.
    Returns the verdict plus the evidence trail."""
    recent = reports[-required_clean_nights:]
    clean_streak = len([r for r in recent if r.clean])
    verdict = (len(recent) == required_clean_nights
               and clean_streak == required_clean_nights)
    return {
        "go": verdict,
        "required_clean_nights": required_clean_nights,
        "observed_nights": len(recent),
        "clean_nights": clean_streak,
        "blocking": [
            f"{r.run_date}: {len(r.by_kind('missing_in_livery'))} missing, "
            f"{len(r.by_kind('money_mismatch'))} money, "
            f"{len(r.by_kind('status_mismatch'))} status"
            for r in recent if not r.clean
        ],
    }
