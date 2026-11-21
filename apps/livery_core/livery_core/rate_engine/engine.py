"""Rate engine — framework-free core (report Ch. 3 §3.2.1).

Replicates the GroundAlliance rate-surface: multiplier / fixed / percentage
models, surges keyed on zone polygon, service class, time-of-day, date,
weekday, and lead time (minutes before pickup). Manual dispatcher overrides
are first-class and always audited.

Acceptance test (Gate 3, report Ch. 9): replay GA-exported historical quotes;
>= 95% of quotes must match within the tolerance band, with override-driven
deltas classified as explained variances. The component target is stricter:
>= 98% exact (report Ch. 3 §3.2.1).

Money is integer minor units everywhere (cents / kobo). No floats.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional


class RuleModel(str, Enum):
    MULTIPLIER = "multiplier"   # base * factor
    FIXED = "fixed"             # absolute price replaces base
    PERCENTAGE = "percentage"   # base * (1 + pct/100) — surcharge or discount


@dataclass(frozen=True)
class Zone:
    """Polygon zone in (lat, lng) vertices. Point-in-polygon is ray casting."""
    name: str
    vertices: tuple[tuple[float, float], ...]

    def contains(self, lat: float, lng: float) -> bool:
        inside = False
        n = len(self.vertices)
        j = n - 1
        for i in range(n):
            yi, xi = self.vertices[i]
            yj, xj = self.vertices[j]
            if ((xi > lng) != (xj > lng)) and (
                lat < (yj - yi) * (lng - xi) / (xj - xi) + yi
            ):
                inside = not inside
            j = i
        return inside


@dataclass(frozen=True)
class RateRule:
    """One pricing rule. A rule matches when ALL set dimensions match."""
    name: str
    model: RuleModel
    value: Decimal                # factor, absolute minor units, or percent
    priority: int = 0             # higher wins when several rules match
    zones: tuple[Zone, ...] = ()
    service_classes: tuple[str, ...] = ()
    time_window: Optional[tuple[time, time]] = None   # local pickup time
    dates: tuple[date, ...] = ()                      # explicit calendar dates
    weekdays: tuple[int, ...] = ()                    # 0=Monday
    max_lead_minutes: Optional[int] = None            # last-minute rule
    min_lead_minutes: Optional[int] = None            # advance-booking rule
    stackable: bool = False       # non-stackable winner ends evaluation

    def matches(self, ctx: "QuoteContext") -> bool:
        if self.zones and not any(
            z.contains(ctx.pickup_lat, ctx.pickup_lng) for z in self.zones
        ):
            return False
        if self.service_classes and ctx.service_class not in self.service_classes:
            return False
        if self.time_window is not None:
            start, end = self.time_window
            t = ctx.pickup_at.time()
            if start <= end:
                if not (start <= t <= end):
                    return False
            else:  # overnight window, e.g. 22:00–05:00
                if not (t >= start or t <= end):
                    return False
        if self.dates and ctx.pickup_at.date() not in self.dates:
            return False
        if self.weekdays and ctx.pickup_at.weekday() not in self.weekdays:
            return False
        if self.max_lead_minutes is not None and ctx.lead_minutes > self.max_lead_minutes:
            return False
        if self.min_lead_minutes is not None and ctx.lead_minutes < self.min_lead_minutes:
            return False
        return True


@dataclass(frozen=True)
class QuoteContext:
    tenant: str
    currency: str                 # EUR (Portugal) or NGN (Nigeria) — never blended
    service_class: str
    base_minor: int               # from distance/time estimate (Valhalla inputs)
    pickup_at: datetime
    pickup_lat: float
    pickup_lng: float
    lead_minutes: int             # minutes between booking and pickup


@dataclass(frozen=True)
class AppliedRule:
    rule: str
    model: str
    delta_minor: int


@dataclass(frozen=True)
class Override:
    """Manual dispatcher override — always audited (who/when/original vs final).
    Parity replay classifies override-driven deltas as explained variances."""
    actor: str
    at: datetime
    final_minor: int
    reason: str


@dataclass
class Quote:
    ctx: QuoteContext
    base_minor: int
    final_minor: int
    applied: list[AppliedRule] = field(default_factory=list)
    override: Optional[Override] = None

    @property
    def overridden(self) -> bool:
        return self.override is not None


def _round_minor(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def price(ctx: QuoteContext, rules: list[RateRule]) -> Quote:
    """Evaluate rules by priority (desc), applying matches until a
    non-stackable rule ends evaluation. Deterministic: equal priority is
    broken by rule name for reproducible parity replays."""
    ordered = sorted(rules, key=lambda r: (-r.priority, r.name))
    current = Decimal(ctx.base_minor)
    applied: list[AppliedRule] = []

    for rule in ordered:
        if not rule.matches(ctx):
            continue
        before = current
        if rule.model is RuleModel.MULTIPLIER:
            current = current * rule.value
        elif rule.model is RuleModel.FIXED:
            current = rule.value
        elif rule.model is RuleModel.PERCENTAGE:
            current = current * (Decimal(1) + rule.value / Decimal(100))
        applied.append(
            AppliedRule(rule=rule.name, model=rule.model.value,
                        delta_minor=_round_minor(current - before))
        )
        if not rule.stackable:
            break

    return Quote(ctx=ctx, base_minor=ctx.base_minor,
                 final_minor=_round_minor(current), applied=applied)


def apply_override(quote: Quote, override: Override) -> Quote:
    """Attach a manual override. The engine keeps the full audit trail:
    computed price + applied rules survive; the override records the delta."""
    if override.final_minor <= 0:
        raise ValueError("override price must be positive")
    if not override.reason.strip():
        raise ValueError("override reason is mandatory — audit trail")
    quote.override = override
    quote.final_minor = override.final_minor
    return quote


def parity_replay(historical: list[tuple[QuoteContext, int]], rules: list[RateRule],
                  tolerance_pct: Decimal = Decimal("2"),
                  explained_overrides: Optional[set[int]] = None) -> dict:
    """Gate 3 acceptance harness. `historical` = (context, GA-quoted minor).
    Returns match rates: exact, within-tolerance, explained-variance."""
    exact = within = explained = 0
    misses: list[int] = []
    for i, (ctx, ga_minor) in enumerate(historical):
        q = price(ctx, rules)
        if q.final_minor == ga_minor:
            exact += 1
            continue
        band = Decimal(ga_minor) * tolerance_pct / Decimal(100)
        if explained_overrides and i in explained_overrides:
            explained += 1
        elif abs(Decimal(q.final_minor) - Decimal(ga_minor)) <= band:
            within += 1
        else:
            misses.append(i)
    n = len(historical) or 1
    return {
        "total": len(historical),
        "exact": exact,
        "within_tolerance": within,
        "explained_variance": explained,
        "misses": misses,
        "exact_rate": exact / n,
        "gate_rate": (exact + within + explained) / n,  # >= 0.95 passes Gate 3
        "component_rate": exact / n,                    # >= 0.98 component target
    }
