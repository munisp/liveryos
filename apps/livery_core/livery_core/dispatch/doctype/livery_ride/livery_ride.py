"""Livery Ride — operational ride record (report Ch. 2 §2.2.1).

Parallel workflow state beside the ERPNext sales pipeline: the Sales Order
carries the commercial lifecycle; this DocType carries the dispatch lifecycle.
Status transitions are validated here (legal graph), never by client code.

rate_trace stores the rate engine's full evaluation trace (applied rules,
deltas, override audit) so every quote is explainable after the fact —
the property GA's rate module has and the parity replay (Gate 3) depends on.
"""

LEGAL_TRANSITIONS = {
    "QUOTED": {"ASSIGNED", "CANCELLED"},
    # ASSIGNED→QUOTED is the board requeue (driver fell through, ride goes
    # back to the board). Phase 2 dispatch board depends on it.
    "ASSIGNED": {"EN_ROUTE", "CANCELLED", "NO_SHOW", "QUOTED"},
    "EN_ROUTE": {"AT_PICKUP", "CANCELLED"},
    "AT_PICKUP": {"IN_PROGRESS", "NO_SHOW"},
    "IN_PROGRESS": {"COMPLETED"},
    "COMPLETED": {"INVOICED"},
    "INVOICED": set(),
    "CANCELLED": set(),
    "NO_SHOW": set(),
}


def can_transition(current: str, target: str) -> bool:
    return target in LEGAL_TRANSITIONS.get(current, set())


# Frappe document class wiring lands in Phase 2 (dispatch board build).
# Kept import-light so the transition map is unit-testable without a bench.
