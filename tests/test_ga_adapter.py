"""GA transition adapter tests — anti-corruption seam (Ch. 7 §7.4.2)."""

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "livery_core"))

from livery_core.adapters.transition.ga_adapter import (  # noqa: E402
    AdapterRowError, ApiSource, CsvExportSource, NotConfigured,
    map_ga_status, parse_ga_datetime, parse_money_minor, row_to_booking,
)

ROW = {
    "Res No": "GA-1001",
    "Account": "Hotel Avenida",
    "Passenger Name": "Maria Silva",
    "Pickup Date": "11/25/2026 09:30",
    "Pickup": "Aeroporto Humberto Delgado, Lisboa",
    "To": "Rua Augusta 24, Lisboa",
    "Vehicle Type": "Sedan",
    "Status": "Confirmed",
    "Total": "€145.50",
    "Ccy": "EUR",
}


def test_status_mapping_including_us_spelling():
    assert map_ga_status("Confirmed") == "QUOTED"
    assert map_ga_status("Dispatched") == "ASSIGNED"
    assert map_ga_status("On Board") == "IN_PROGRESS"
    assert map_ga_status("Canceled") == "CANCELLED"   # US spelling
    assert map_ga_status("No Show") == "NO_SHOW"
    with pytest.raises(AdapterRowError):
        map_ga_status("Pending Review")  # unknown → quarantine, never guess


def test_money_minor_parsing():
    assert parse_money_minor("€1,234.56", "EUR") == 123456
    assert parse_money_minor("1234.56", "EUR") == 123456
    assert parse_money_minor("₦50000", "NGN") == 5000000
    assert parse_money_minor("$0.99", "USD") == 99
    with pytest.raises(AdapterRowError):
        parse_money_minor("1.234,56", "EUR")   # European format → quarantine
    with pytest.raises(AdapterRowError):
        parse_money_minor("", "EUR")
    with pytest.raises(AdapterRowError):
        parse_money_minor("100", "JPY")        # 0-decimal currency unsupported


def test_ga_datetime_us_and_iso():
    from datetime import timezone
    assert parse_ga_datetime("11/25/2026 09:30") == datetime(2026, 11, 25, 9, 30, tzinfo=timezone.utc)
    assert parse_ga_datetime("2026-11-25 09:30:00").year == 2026
    with pytest.raises(AdapterRowError):
        parse_ga_datetime("next tuesday")


def test_row_to_booking_canonical():
    b = row_to_booking(ROW)
    assert b.external_id == "GA-1001"
    assert b.status == "QUOTED"                 # Confirmed → QUOTED
    assert b.quoted_minor == 14550 and b.currency == "EUR"
    assert b.service_class == "sedan"
    assert b.provenance == "ga_import"


def test_csv_source_quarantines_bad_rows_keeps_batch():
    rows = [
        ROW,
        {"Res No": "GA-1002", "Status": "Weird", "Pickup Date": "11/25/2026 10:00"},
        {"Status": "Confirmed"},  # no reservation id
        {**ROW, "Res No": "GA-1003", "Total": "1.234,56"},  # ambiguous money
    ]
    result = CsvExportSource().import_rows(rows)
    assert result.ok == 1
    assert len(result.quarantined) == 3
    assert result.quarantined[0][0] == 3        # spreadsheet row number (header=1)
    assert "unmapped GA status" in result.quarantined[0][2]
    assert "no reservation id" in result.quarantined[1][2]
    assert "ambiguous" in result.quarantined[2][2]


def test_csv_source_handles_missing_price():
    row = {**ROW, "Total": ""}
    result = CsvExportSource().import_rows([row])
    assert result.ok == 1 and result.bookings[0].quoted_minor is None


def test_api_source_blocked_until_gate1_evidence():
    with pytest.raises(NotConfigured) as exc:
        ApiSource()
    assert "Gate 1" in str(exc.value)
    # with evidence it constructs (implementation itself lands in Gate 2)
    src = ApiSource(base_url="https://api.ga.example", credentials={"k": "v"},
                    api_docs_evidence="GA written docs received 2026-12-01")
    assert src.name == "ga_api"
