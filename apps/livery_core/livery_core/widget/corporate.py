"""Corporate booking tool domain — accounts, PO policy, cost centers,
traveler profiles, approval rules (GA corporate-tool parity).

Corporate rides bill monthly to the account, not to a card — so the policy
gate matters BEFORE the ride exists. Every rejection carries a readable
reason (same discipline as affiliate eligibility).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional, Tuple


class CorporatePolicyError(ValueError):
    """Booking violates account policy — rejected with a readable reason."""


@dataclass(frozen=True)
class CostCenter:
    code: str
    name: str
    monthly_budget_minor: Optional[int] = None   # None = uncapped
    spent_minor: int = 0

    def would_exceed(self, amount_minor: int) -> bool:
        return (self.monthly_budget_minor is not None and
                self.spent_minor + amount_minor > self.monthly_budget_minor)


@dataclass(frozen=True)
class TravelerProfile:
    traveler_id: str
    name: str
    email: str
    default_cost_center: Optional[str] = None
    max_service_class: str = "executive"     # class ladder below
    self_book: bool = True                    # False = booker/assistant only


@dataclass(frozen=True)
class CorporateAccount:
    account_id: str
    tenant: str
    name: str
    currency: str                              # EUR | NGN — never blended
    po_required: bool = True
    require_approval_over_minor: Optional[int] = None  # None = no approvals
    cost_centers: Tuple[CostCenter, ...] = ()
    travelers: Tuple[TravelerProfile, ...] = ()
    active: bool = True

    def center(self, code: str) -> CostCenter:
        for c in self.cost_centers:
            if c.code == code:
                return c
        raise CorporatePolicyError(f"unknown cost center {code!r}")

    def traveler(self, traveler_id: str) -> TravelerProfile:
        for t in self.travelers:
            if t.traveler_id == traveler_id:
                return t
        raise CorporatePolicyError(f"unknown traveler {traveler_id!r}")


# Service-class ladder — a traveler capped at "business" cannot book
# "executive", but can always book below their cap.
_CLASS_LADDER = ("economy", "business", "executive", "sprinter")


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    needs_approval: bool
    reason: str
    approver: Optional[str] = None       # account manager / travel manager


def evaluate_booking(account: CorporateAccount, *, traveler_id: str,
                     service_class: str, amount_minor: int,
                     cost_center_code: str, po_number: Optional[str],
                     currency: str) -> ApprovalDecision:
    """The pre-booking gate. Raises CorporatePolicyError on hard violations;
    returns a decision when the booking may proceed (possibly pending
    approval)."""
    if not account.active:
        raise CorporatePolicyError(f"account {account.account_id} is inactive")
    if currency.upper() != account.currency.upper():
        raise CorporatePolicyError(
            f"account bills {account.currency}; booking currency {currency} "
            "mismatched — LiveryOS never blends currencies")
    traveler = account.traveler(traveler_id)
    if not traveler.self_book:
        raise CorporatePolicyError(
            f"traveler {traveler.name} may not self-book — a booker must place it")
    if _CLASS_LADDER.index(service_class) > _CLASS_LADDER.index(
            traveler.max_service_class):
        raise CorporatePolicyError(
            f"{service_class} exceeds traveler cap {traveler.max_service_class}")
    if account.po_required and not (po_number or "").strip():
        raise CorporatePolicyError(
            f"account {account.name} requires a PO number for every booking")
    center = account.center(cost_center_code)
    if center.would_exceed(amount_minor):
        raise CorporatePolicyError(
            f"cost center {center.code} monthly budget would be exceeded "
            f"(spent {center.spent_minor} + {amount_minor} > "
            f"{center.monthly_budget_minor} minor units)")
    needs = (account.require_approval_over_minor is not None and
             amount_minor > account.require_approval_over_minor)
    return ApprovalDecision(
        approved=not needs, needs_approval=needs,
        reason=("amount above approval threshold — routed to travel manager"
                if needs else "within policy"),
        approver="travel-manager" if needs else None,
    )
