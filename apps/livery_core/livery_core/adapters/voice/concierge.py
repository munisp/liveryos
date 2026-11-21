"""Voice concierge orchestration — what the bot SAYS.

The concierge is LiveryOS' front-of-house: it answers the booking line,
reads quotes and statuses aloud, and escalates to humans. The golden rule,
identical to the UI contract: numbers come from the API and are read
VERBATIM, with their provenance labels attached. An ETA is spoken as
"about 12 minutes, estimate" — the word "estimate" is part of the
utterance, not fine print.

Rendering is deterministic (templates + API payloads). The LLM in the
Pipecat pipeline handles turn-taking and intent extraction; it NEVER
authors numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ConciergePersona:
    market: str                  # "PT" | "NG"
    language: str                # "pt-PT" | "en-NG"
    greeting: str
    disclosure: str              # bot self-identification (mandatory)
    escalation_offer: str
    closing: str


_PERSONAS = {
    "PT": ConciergePersona(
        market="PT", language="pt-PT",
        greeting="Bom dia, LiveryOS. Em que posso ajudar na sua viagem?",
        disclosure=("Sou a assistente automática da LiveryOS. Posso reservar, "
                    "consultar ou cancelar a sua viagem. Diga 'humano' a "
                    "qualquer momento para falar com o despacho."),
        escalation_offer="Quer que ligue ao despacho agora?",
        closing="Obrigado. Boa viagem.",
    ),
    "NG": ConciergePersona(
        market="NG", language="en-NG",
        greeting="Hello, this is LiveryOS. How can I help your trip today?",
        disclosure=("I am the LiveryOS automated assistant. I can book, "
                    "check, or cancel your ride. Say 'human' anytime to "
                    "reach dispatch."),
        escalation_offer="Should I connect you to dispatch now?",
        closing="Thank you. Safe trip.",
    ),
}


def persona_for_market(market: str) -> ConciergePersona:
    try:
        return _PERSONAS[market.upper()]
    except KeyError:
        raise ValueError(f"no concierge persona for market {market!r} — "
                         "add one explicitly; the concierge never ad-libs "
                         "in a market it wasn't configured for")


@dataclass(frozen=True)
class SpokenResponse:
    """One concierge utterance, ready for TTS. `verbatim_fields` lists which
    payload values MUST appear unmodified in the text (testable contract)."""
    text: str
    language: str
    intent: str                  # "quote" | "status" | "greeting" | ...
    verbatim_fields: tuple = ()


def greet(persona: ConciergePersona, *, first_contact: bool = True) -> SpokenResponse:
    text = f"{persona.greeting} {persona.disclosure}" if first_contact \
        else persona.greeting
    return SpokenResponse(text, persona.language, "greeting")


def _fmt_money(minor: int, currency: str) -> str:
    amount = minor / 100
    if currency == "NGN":
        return f"{amount:,.2f} naira"
    if currency == "EUR":
        return f"{amount:,.2f} euros"
    raise ValueError(f"unsupported currency {currency!r} — never guess a "
                     "currency's spoken name")


def render_quote(persona: ConciergePersona, *, quoted_minor: int,
                 currency: str, service_class: str,
                 pickup_at_human: str, engine_version: str) -> SpokenResponse:
    """Read a rate-engine quote aloud. Engine version is NOT spoken (riders
    don't care) but IS carried in verbatim_fields provenance for logging."""
    price = _fmt_money(quoted_minor, currency)
    text = (f"Your {service_class} ride at {pickup_at_human} costs {price}. "
            f"Should I confirm the booking?")
    return SpokenResponse(text, persona.language, "quote",
                          verbatim_fields=(price, service_class,
                                           pickup_at_human))


def render_status(persona: ConciergePersona, *, status: str,
                  eta_minutes: Optional[int], eta_source: str,
                  driver_name: Optional[str]) -> SpokenResponse:
    """Read ride status. The eta_source word is SPOKEN — 'about 12 minutes,
    estimate' — because a confident-sounding wrong number is worse than no
    number. When eta_source is 'none', we say we don't have an ETA."""
    human = status.lower().replace("_", " ")
    if eta_minutes is None or eta_source == "none":
        eta_part = "I don't have a reliable arrival estimate yet."
    else:
        eta_part = f"arriving in about {eta_minutes} minutes, {eta_source}."
    driver_part = f" Your driver is {driver_name}." if driver_name else ""
    text = f"Your ride is {human}.{driver_part} {eta_part}"
    return SpokenResponse(text, persona.language, "status",
                          verbatim_fields=(human,))


def render_escalation(persona: ConciergePersona, *, after_hours: bool,
                      human_rostered: bool) -> SpokenResponse:
    """Escalation honesty: never promise a human who isn't rostered."""
    if after_hours and not human_rostered:
        return SpokenResponse(
            f"{persona.escalation_offer.split('?')[0]} is not available "
            "right now. I can take a message and dispatch will call you "
            "back first thing.", persona.language, "escalation")
    return SpokenResponse(persona.escalation_offer, persona.language,
                          "escalation")
