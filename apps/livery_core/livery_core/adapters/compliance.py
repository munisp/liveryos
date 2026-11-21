"""Chauffeur compliance — daily expiry sweep (gap-list item 12; hooks.py
daily target). The pure decision logic is framework-free; the bench wrapper
is thin.

A chauffeur whose licence or medical lapses must drop out of dispatch
eligibility (dispatch.board.DriverState.compliance_valid reads this) and
trigger an alert well before the date — not after the first failed roadside
check. Portugal: TVDE driver certification + criminal-record currency;
Nigeria: LASG/LASTMA e-hailing permits for Lagos operators + medical.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class ComplianceRecord:
    driver_id: str
    licence_expiry: Optional[date]        # None = no date on file → non-compliant
    medical_expiry: Optional[date]
    market: str = "PT"                    # PT | NG — drives which docs are mandatory


@dataclass(frozen=True)
class ComplianceStatus:
    driver_id: str
    compliant: bool
    expiring_within_days: Optional[int]   # min days to nearest expiry, if compliant
    problems: tuple[str, ...]


def evaluate(rec: ComplianceRecord, today: date, warn_days: int = 30) -> ComplianceStatus:
    """Both documents mandatory, both must be in date. Missing date = not
    compliant (you cannot prove validity you don't have)."""
    problems: list[str] = []
    nearest: Optional[int] = None
    for label, expiry in (("licence", rec.licence_expiry), ("medical", rec.medical_expiry)):
        if expiry is None:
            problems.append(f"{label} expiry not on file")
            continue
        days = (expiry - today).days
        if days < 0:
            problems.append(f"{label} expired {-days} days ago")
        else:
            nearest = days if nearest is None else min(nearest, days)
            if days <= warn_days:
                problems.append(f"{label} expires in {days} days")
    compliant = not any("not on file" in p or "expired" in p for p in problems)
    return ComplianceStatus(
        rec.driver_id, compliant,
        nearest if compliant else None,
        tuple(problems),
    )


def sweep(records: list[ComplianceRecord], today: date,
          warn_days: int = 30) -> dict:
    """Whole-fleet daily sweep → counts + the actionable lists the console
    renders (expired = pull from dispatch NOW; expiring = chase paperwork)."""
    expired, expiring, ok = [], [], []
    for rec in records:
        st = evaluate(rec, today, warn_days)
        if not st.compliant:
            expired.append(st)
        elif st.problems:
            expiring.append(st)
        else:
            ok.append(st)
    return {"total": len(records), "ok": len(ok),
            "expiring": expiring, "non_compliant": expired}


def check_chauffeur_expiry():  # pragma: no cover — bench only
    """hooks.py daily target. Reads HRMS Driver/Employee expiry custom
    fields, runs the sweep, creates Frappe Notifications for actionables,
    and stamps livery_compliance_valid on the Driver for the board."""
    import frappe  # noqa: PLC0415
    from datetime import date as _date

    today = _date.today()
    drivers = frappe.get_all(
        "Driver",
        fields=["name", "license_expiry_date", "livery_medical_expiry",
                "livery_market"],
    )
    records = [
        ComplianceRecord(
            driver_id=d["name"],
            licence_expiry=d.get("license_expiry_date"),
            medical_expiry=d.get("livery_medical_expiry"),
            market=d.get("livery_market") or "PT",
        )
        for d in drivers
    ]
    result = sweep(records, today)
    for st in result["non_compliant"] + result["expiring"]:
        frappe.get_doc({
            "doctype": "Notification Log",
            "subject": f"Chauffeur compliance: {st.driver_id} — {'; '.join(st.problems)}",
            "for_user": "Administrator",
            "type": "Alert",
            "document_type": "Driver",
            "document_name": st.driver_id,
        }).insert(ignore_permissions=True)
        frappe.db.set_value("Driver", st.driver_id,
                            "livery_compliance_valid", 1 if st.compliant else 0)
    for st in result["non_compliant"]:
        frappe.logger("livery.compliance").warning(
            f"{st.driver_id} NON-COMPLIANT: {'; '.join(st.problems)}")
