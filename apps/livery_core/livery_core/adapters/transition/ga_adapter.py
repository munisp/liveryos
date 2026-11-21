"""GroundAlliance transition adapter — anti-corruption layer (Ch. 7 §7.4.2).

Everything GA-specific lives HERE and nowhere else. Decommissioning GA
changes only this package. Two sources implement the same seam:

- CsvExportSource: the guaranteed path. Works from GA's scheduled CSV
  exports (always available, even on hostile Gate 1 outcomes). Batch,
  cadence-bound, honest about it.
- ApiSource: the preferred path IF Gate 1 delivers documented API +
  sandbox. Until then it raises NotConfigured — we do not guess at an
  undocumented API surface.

GA rows become CanonicalBooking records with provenance="ga_import" so
parallel-run reconciliation (report Ch. 8) can always tell imported truth
from native bookings. Money strings are parsed with Decimal into integer
minor units; ambiguous rows are quarantined per-row, never silently
coerced and never allowed to kill the batch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable, Optional


class NotConfigured(RuntimeError):
    """Raised by ApiSource until Gate 1 delivers API docs + sandbox."""


class AdapterRowError(ValueError):
    """One unparseable GA row — quarantined, batch continues."""


# GA reservation statuses observed in exports → Livery Ride statuses.
# Unknown statuses quarantine the row rather than guess (status drives
# the dispatch state machine; a wrong guess corrupts parallel-run counts).
GA_STATUS_MAP = {
    "QUOTED": "QUOTED",
    "CONFIRMED": "QUOTED",          # GA confirmed-but-undispatched ≈ our QUOTED
    "DISPATCHED": "ASSIGNED",
    "EN ROUTE": "EN_ROUTE",
    "ENROUTE": "EN_ROUTE",
    "ARRIVED": "AT_PICKUP",
    "ON BOARD": "IN_PROGRESS",
    "ONBOARD": "IN_PROGRESS",
    "IN PROGRESS": "IN_PROGRESS",
    "COMPLETED": "COMPLETED",
    "INVOICED": "INVOICED",
    "BILLED": "INVOICED",
    "CANCELLED": "CANCELLED",
    "CANCELED": "CANCELLED",
    "NO SHOW": "NO_SHOW",
    "NOSHOW": "NO_SHOW",
}


def map_ga_status(raw: str) -> str:
    key = (raw or "").strip().upper()
    if key not in GA_STATUS_MAP:
        raise AdapterRowError(f"unmapped GA status {raw!r}")
    return GA_STATUS_MAP[key]


def parse_money_minor(raw: str, currency: str) -> int:
    """'€1,234.56' / '1234.56' / '₦50000' → minor units. 2-decimal currencies
    (EUR, NGN, USD). Thousand separators tolerated; symbols stripped.
    Ambiguity (e.g. European '1.234,56') is NOT auto-resolved — quarantine."""
    if currency not in ("EUR", "NGN", "USD", "GBP"):
        raise AdapterRowError(f"unsupported currency {currency!r}")
    s = (raw or "").strip()
    for sym in ("€", "₦", "$", "£", "EUR", "NGN", "USD", "GBP"):
        s = s.replace(sym, "")
    s = s.strip()
    if not s:
        raise AdapterRowError("empty money value")
    # European format ("1.234,56") puts the comma AFTER the dot — ambiguous
    # with US thousands/decimal, so quarantine rather than guess.
    if "," in s and "." in s and s.rindex(",") > s.rindex("."):
        raise AdapterRowError(f"ambiguous money value {raw!r} (European format? — quarantine)")
    s = s.replace(",", "")
    if s.count(".") > 1:
        raise AdapterRowError(f"ambiguous money value {raw!r} (European format? — quarantine)")
    try:
        major = Decimal(s)
    except InvalidOperation:
        raise AdapterRowError(f"unparseable money value {raw!r}")
    return int((major * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_ga_datetime(raw: str) -> datetime:
    """GA exports are US-centric: 'MM/DD/YYYY HH:MM' or ISO. Both accepted;
    anything else quarantines. Naive → marked UTC with a provenance caveat
    the caller must resolve against the tenant's market timezone."""
    s = (raw or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M %p", "%m/%d/%Y"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise AdapterRowError(f"unparseable GA datetime {raw!r}")


@dataclass(frozen=True)
class CanonicalBooking:
    """The platform's internal truth for one reservation — same shape
    whether it came from GA CSV, GA API, or natively."""
    external_id: str                       # GA reservation number
    customer: str
    passenger: str
    pickup_at: datetime
    pickup_address: str
    dropoff_address: str
    service_class: str
    status: str                            # Livery Ride status (mapped)
    quoted_minor: Optional[int]            # None = GA row had no price
    currency: str
    provenance: str = "ga_import"


@dataclass
class ImportResult:
    bookings: list[CanonicalBooking] = field(default_factory=list)
    quarantined: list[tuple[int, dict, str]] = field(default_factory=list)  # (row_no, row, error)

    @property
    def ok(self) -> int:
        return len(self.bookings)


# Canonical column mapping for GA's standard reservation export. If Gate 1
# shows a different export layout, change THIS dict only.
GA_CSV_COLUMNS = {
    "reservation": ("reservation", "res no", "confirmation", "conf no"),
    "customer": ("customer", "account", "client"),
    "passenger": ("passenger", "passenger name"),
    "pickup_at": ("pickup datetime", "pickup date", "date/time"),
    "pickup_address": ("pickup", "pickup address", "from"),
    "dropoff_address": ("dropoff", "dropoff address", "to"),
    "service_class": ("service class", "vehicle type", "class"),
    "status": ("status",),
    "price": ("price", "total", "amount", "rate"),
    "currency": ("currency", "ccy"),
}


def _col(row: dict, canonical: str) -> str:
    """Case-insensitive column lookup against GA_CSV_COLUMNS aliases."""
    lowered = {k.strip().lower(): v for k, v in row.items()}
    for alias in GA_CSV_COLUMNS[canonical]:
        if alias in lowered:
            return str(lowered[alias]).strip()
    return ""


def row_to_booking(row: dict) -> CanonicalBooking:
    ext_id = _col(row, "reservation")
    if not ext_id:
        raise AdapterRowError("no reservation id")
    currency = (_col(row, "currency") or "USD").upper()
    price_raw = _col(row, "price")
    quoted = parse_money_minor(price_raw, currency) if price_raw else None
    return CanonicalBooking(
        external_id=ext_id,
        customer=_col(row, "customer"),
        passenger=_col(row, "passenger"),
        pickup_at=parse_ga_datetime(_col(row, "pickup_at")),
        pickup_address=_col(row, "pickup_address"),
        dropoff_address=_col(row, "dropoff_address"),
        service_class=(_col(row, "service_class") or "sedan").lower(),
        status=map_ga_status(_col(row, "status")),
        quoted_minor=quoted,
        currency=currency,
    )


class CsvExportSource:
    """Import from GA CSV export rows (list of dicts, e.g. csv.DictReader).
    Per-row quarantine: a bad row is recorded with its error and the batch
    continues — the reconciliation report (Ch. 8) shows exactly what was
    left behind and why."""

    name = "ga_csv_export"

    def import_rows(self, rows: Iterable[dict]) -> ImportResult:
        result = ImportResult()
        for i, row in enumerate(rows, start=2):  # row 1 = header
            try:
                result.bookings.append(row_to_booking(row))
            except AdapterRowError as e:
                result.quarantined.append((i, dict(row), str(e)))
        return result


class ApiSource:
    """GA REST API source. BLOCKED on Gate 1 item 1 (documented API +
    sandbox). The report found marketing claims but no public API docs;
    implementing against an undocumented surface would be guessing, so
    this raises until evidence lands — that IS the Gate 2 signal."""

    name = "ga_api"

    def __init__(self, base_url: Optional[str] = None,
                 credentials: Optional[dict] = None,
                 api_docs_evidence: Optional[str] = None):
        if not api_docs_evidence:
            raise NotConfigured(
                "GA API access is not evidenced (Gate 1 item 1 unresolved). "
                "Use CsvExportSource until written API docs + sandbox arrive. "
                "See docs/phase0/ga-diligence-email.md demand 1.")
        self.base_url = base_url
        self.credentials = credentials or {}
        self.evidence = api_docs_evidence

    def import_rows(self, rows=None) -> ImportResult:  # pragma: no cover
        raise NotImplementedError("lands in Gate 2 once the documented API surface is known")
