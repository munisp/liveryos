"""Pipecat voice-agent pipeline builder (https://github.com/pipecat-ai/pipecat).

Pipecat orchestrates STT → LLM → TTS as a realtime voice pipeline on
WebRTC (LiveKit transport). LiveryOS uses it for the after-hours booking
line and the "where is my driver" line.

This module builds the DECLARATIVE spec the bot runner consumes. It does
not run a bot, and a spec existing is NOT a call having happened — call
records come from LiveKit/Pipecat webhooks.

Hard honesty rules baked into the spec:
- The bot must introduce itself as an automated assistant (`bot_disclosure`).
- The bot may NOT confirm bookings free-form; it emits structured intents
  (create_booking, status_lookup, cancel_request) that the booking
  pipeline validates like any other API call.
- If confidence is low or the caller asks for a human, the bot escalates
  to a masked LiveKit room with dispatch (or takes a voicemail after
  hours — it never pretends a human is coming when none is rostered).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass(frozen=True)
class PipecatPipelineSpec:
    """Declarative pipeline config — consumed by the pipecat runner service."""
    name: str
    transport: str                       # "livekit"
    stt: dict                            # e.g. {"provider": "whisper", "model": "large-v3-turbo"}
    llm: dict                            # e.g. {"provider": "openai-compatible", "model": "...", "endpoint": ...}
    tts: dict                            # e.g. {"provider": "piper", "voice": "pt-PT-default"}
    system_prompt: str
    allowed_intents: tuple = ("create_booking", "status_lookup", "cancel_request")
    bot_disclosure: str = (
        "This is LiveryOS automated assistant. I can book, check, or cancel "
        "a ride. Say 'human' at any time to reach dispatch."
    )
    escalation_room_prefix: str = "livery-escalation-"
    after_hours_mode: str = "voicemail"  # voicemail | escalation — NEVER fake-human
    language: str = "en"
    recording_consent_prompt: bool = True

    def to_dict(self) -> dict:
        d = asdict(self)
        d["allowed_intents"] = list(self.allowed_intents)
        return d


_SYSTEM_BOOKING_LINE = (
    "You are the LiveryOS phone booking assistant. You collect pickup, "
    "dropoff, time, passenger count, and service class, then emit a "
    "create_booking intent — you never invent prices or ETAs; you read "
    "quotes returned by the booking tool verbatim. If asked anything "
    "outside booking/status/cancel, offer to connect the caller to dispatch."
)

_SYSTEM_STATUS_LINE = (
    "You are the LiveryOS ride-status assistant. You identify the caller's "
    "booking by phone number or reference, emit a status_lookup intent, "
    "and read the result verbatim — including when the answer is 'no "
    "driver assigned yet'. You never guess an ETA."
)


def build_pipeline_spec(*, name: str, purpose: str, language: str,
                        stt: dict, llm: dict, tts: dict,
                        after_hours_mode: str = "voicemail") -> PipecatPipelineSpec:
    if purpose not in ("booking_line", "status_line"):
        raise ValueError(f"unknown bot purpose {purpose!r}")
    if after_hours_mode not in ("voicemail", "escalation"):
        raise ValueError("after_hours_mode must be voicemail|escalation — "
                         "pretending a human is rostered when none is, is banned")
    return PipecatPipelineSpec(
        name=name,
        transport="livekit",
        stt=stt, llm=llm, tts=tts,
        system_prompt=(_SYSTEM_BOOKING_LINE if purpose == "booking_line"
                       else _SYSTEM_STATUS_LINE),
        after_hours_mode=after_hours_mode,
        language=language,
    )


def longcat_tts_spec(*, endpoint: Optional[str], model_key: str = "longcat-1b",
                     voice_profile_id: Optional[str] = None) -> dict:
    """TTS provider spec for the pipeline: LongCat-AudioDiT primary, piper
    fallback. `provenance_per_utterance` instructs the runner to log which
    engine actually spoke — a swapped voice is never silent. When endpoint
    is None the spec honestly declares longcat disabled and piper primary.
    """
    if endpoint:
        return {
            "provider": "longcat-audiodit",
            "endpoint": endpoint,
            "model_key": model_key,
            "sample_rate": 24000,
            "voice_profile_id": voice_profile_id,
            "guidance_method": "apg",
            "fallback": {"provider": "piper"},
            "provenance_per_utterance": True,
            "self_hosted": True,
        }
    return {
        "provider": "piper",
        "note": "longcat-tts not configured — piper primary (labeled in "
                "utterance provenance)",
        "provenance_per_utterance": True,
        "self_hosted": True,
    }


def voice_agent_config(market: str, *, llm_endpoint: str, llm_model: str,
                       after_hours_mode: str = "voicemail",
                       longcat_endpoint: Optional[str] = None,
                       longcat_model_key: str = "longcat-1b",
                       voice_profile_id: Optional[str] = None) -> "list[PipecatPipelineSpec]":
    """Standard two-bot deployment for a market.

    Market picks voice/language defaults: PT → pt-PT + English; NG → en-NG
    (+ pidgin-tolerant STT note). Both bots share one self-hostable stack.
    TTS is LongCat-AudioDiT when its GPU service is configured, piper
    otherwise — the spec records which, honestly.
    """
    market = market.upper()
    if market == "PT":
        lang = "pt-PT"
    elif market == "NG":
        lang = "en-NG"
    else:
        raise ValueError(f"unknown market {market!r}")
    stt = {"provider": "whisper", "model": "large-v3-turbo",
           "self_hosted": True}
    llm = {"provider": "openai-compatible", "endpoint": llm_endpoint,
           "model": llm_model}
    tts = longcat_tts_spec(endpoint=longcat_endpoint,
                           model_key=longcat_model_key,
                           voice_profile_id=voice_profile_id)
    return [
        build_pipeline_spec(name=f"{market.lower()}-booking-line",
                            purpose="booking_line", language=lang,
                            stt=stt, llm=llm, tts=tts,
                            after_hours_mode=after_hours_mode),
        build_pipeline_spec(name=f"{market.lower()}-status-line",
                            purpose="status_line", language=lang,
                            stt=stt, llm=llm, tts=tts,
                            after_hours_mode=after_hours_mode),
    ]
