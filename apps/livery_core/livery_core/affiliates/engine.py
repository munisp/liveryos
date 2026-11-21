"""Affiliate engine — the moat component (report Ch. 3 §3.2.6).

The report's core strategic finding: the moat in this industry is the
affiliate network, not the software. This engine makes farm-in/farm-out a
first-class, configuration-driven capability: "GNet partner" is a row in
`affiliate_partners` (infra/postgres-init.sql), not a special case.

Rules:
- Eligibility is deterministic and explainable: direction, active flag,
  and blocking lists (airports, zones, service classes) — every rejection
  carries a reason a dispatcher can read.
- Money is integer minor units. Partner share = ROUND_HALF_UP(amount *
  split_pct/100); the operator keeps the remainder, so the split NEVER
  loses or invents a cent/kobo across the two sides.
- Every settlement records provenance (which engine version, which rule)
  so parallel-run reconciliation (Ch. 8) can re-derive every number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

ENGINE_VERSION = "1.0.0"


@dataclass(frozen=True)
class BlockingRule:
    """One blocking entry. Exactly one dimension is set per rule."""
    airport: Optional[str] = None          # IATA, e.g. "LIS", "LOS"
    zone: Optional[str] = None             # named zone, e.g. "lagos-island"
    service_class: Optional[str] = None    # e.g. "sprinter"

    def blocks(self, req: "FarmRequest") -> bool:
        if self.airport and self.airport.upper() in (req.pickup_airport or "", req.dropoff_airport or ""):
            return True
        if self.zone and self.zone in (req.pickup_zone, req.dropoff_zone):
            return True
        if self.service_class and self.service_class == req.service_class:
            return True
        return False


@dataclass(frozen=True)
class Partner:
    partner_ref: str                       # Supplier ref on the bench
    direction: str                         # farm_in | farm_out | both
    split_pct: Optional[Decimal] = None    # partner's share %; None = quote per booking
    blocked: tuple[BlockingRule, ...] = ()
    active: bool = True
    gres_system_id: Optional[str] = None   # GNet id once connected

    def allows_farm_out(self) -> bool:
        return self.direction in ("farm_out", "both")

    def allows_farm_in(self) -> bool:
        return self.direction in ("farm_in", "both")


@dataclass(frozen=True)
class FarmRequest:
    booking_ref: str
    tenant: str
    service_class: str
    amount_minor: int
    currency: str                          # EUR | NGN — never blended
    pickup_airport: Optional[str] = None
    dropoff_airport: Optional[str] = None
    pickup_zone: Optional[str] = None
    dropoff_zone: Optional[str] = None


@dataclass(frozen=True)
class EligibilityResult:
    partner_ref: str
    eligible: bool
    reason: str


@dataclass(frozen=True)
class FarmSettlement:
    booking_ref: str
    tenant: str
    partner_ref: str
    direction: str
    gross_minor: int
    partner_minor: Optional[int]           # None when split is per-booking quote
    operator_minor: Optional[int]
    currency: str
    split_pct: Optional[Decimal]
    engine_version: str = ENGINE_VERSION
    settled_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def check_eligibility(req: FarmRequest, partner: Partner,
                      direction: str = "farm_out") -> EligibilityResult:
    if not partner.active:
        return EligibilityResult(partner.partner_ref, False, "partner inactive")
    if direction == "farm_out" and not partner.allows_farm_out():
        return EligibilityResult(partner.partner_ref, False,
                                 f"direction {partner.direction} does not allow farm-out")
    if direction == "farm_in" and not partner.allows_farm_in():
        return EligibilityResult(partner.partner_ref, False,
                                 f"direction {partner.direction} does not allow farm-in")
    for rule in partner.blocked:
        if rule.blocks(req):
            dim = (f"airport={rule.airport}" if rule.airport else
                   f"zone={rule.zone}" if rule.zone else f"class={rule.service_class}")
            return EligibilityResult(partner.partner_ref, False, f"blocking rule: {dim}")
    return EligibilityResult(partner.partner_ref, True, "eligible")


def select_partner(req: FarmRequest, partners: list[Partner],
                   preferred_ref: Optional[str] = None) -> tuple[Optional[Partner], list[EligibilityResult]]:
    """Pick a farm-out partner. A valid preferred partner always wins
    (dispatcher intent is sovereign); otherwise the partner with a
    configured split wins over per-booking quoting (predictable money),
    ties broken by partner_ref for determinism. All rejections returned."""
    results = [check_eligibility(req, p) for p in partners]
    eligible = [p for p, r in zip(partners, results) if r.eligible]
    if preferred_ref:
        for p in eligible:
            if p.partner_ref == preferred_ref:
                return p, results
    ranked = sorted(eligible, key=lambda p: (p.split_pct is None, p.partner_ref))
    return (ranked[0] if ranked else None), results


def split_minor(amount_minor: int, split_pct: Decimal) -> tuple[int, int]:
    """(partner_minor, operator_minor). Partner share rounds HALF_UP;
    operator takes the remainder. Sum is ALWAYS amount_minor — no penny
    is lost or created, in either currency."""
    if amount_minor < 0:
        raise ValueError("amount must be non-negative")
    if not (Decimal(0) < split_pct <= Decimal(100)):
        raise ValueError(f"split_pct out of range: {split_pct}")
    partner = int((Decimal(amount_minor) * split_pct / Decimal(100)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP))
    return partner, amount_minor - partner


def settle_farm_out(req: FarmRequest, partner: Partner) -> FarmSettlement:
    """Create the settlement record for a farmed-out booking. When the
    partner has no configured split, money fields are None — the booking
    goes out as a per-booking quote and settles manually; honestly None,
    never zero."""
    if partner.split_pct is None:
        return FarmSettlement(req.booking_ref, req.tenant, partner.partner_ref,
                              "farm_out", req.amount_minor, None, None,
                              req.currency, None)
    partner_minor, operator_minor = split_minor(req.amount_minor, partner.split_pct)
    return FarmSettlement(req.booking_ref, req.tenant, partner.partner_ref,
                          "farm_out", req.amount_minor, partner_minor,
                          operator_minor, req.currency, partner.split_pct)


def settle_farm_in(req: FarmRequest, partner: Partner, our_share_pct: Decimal) -> FarmSettlement:
    """We are the affiliate RECEIVING a farmed-in booking: our share is
    what WE earn; the originator keeps the rest. Same no-penny-lost math."""
    our_minor, originator_minor = split_minor(req.amount_minor, our_share_pct)
    return FarmSettlement(req.booking_ref, req.tenant, partner.partner_ref,
                          "farm_in", req.amount_minor, our_minor,
                          originator_minor, req.currency, our_share_pct)
