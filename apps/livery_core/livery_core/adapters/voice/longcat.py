"""LongCat-AudioDiT synthesis config + engine selection.

The model runs as a separate GPU service (services/longcat_tts_server.py,
compose service `longcat-tts` behind the "gpu" profile). This module is the
client-side contract: what a request looks like, how durations are
estimated, and — critically — WHICH engine actually produced the audio
(provenance on every result).

Engine selection is honest degradation:
    longcat configured + reachable  → "longcat-1b" / "longcat-3.5b"
    otherwise                        → "piper-fallback" (labeled!)
    neither                          → "none" — caller renders text, not fake audio
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional
from uuid import uuid4


SAMPLE_RATE = 24000           # LongCat-AudioDiT native output rate
LATENT_FPS = 25               # Wav-VAE latent frames per second (24k/960 hop)
MODELS = {
    "longcat-1b": "meituan-longcat/LongCat-AudioDiT-1B",
    "longcat-3.5b": "meituan-longcat/LongCat-AudioDiT-3.5B",
}


@dataclass(frozen=True)
class LongCatTTSConfig:
    """Serving config. `endpoint` is the HTTP server, `model_key` the size.
    guidance: "apg" (adaptive projection guidance — repo default for
    cloning) or "cfg" (classic classifier-free, plain TTS)."""
    model_key: str = "longcat-1b"
    endpoint: Optional[str] = None          # e.g. http://longcat-tts:8100
    steps: int = 16
    cfg_strength: float = 4.0
    guidance_method: str = "apg"
    max_text_chars: int = 500

    def __post_init__(self):
        if self.model_key not in MODELS:
            raise ValueError(f"unknown model {self.model_key!r}; "
                             f"one of {sorted(MODELS)}")
        if self.guidance_method not in ("apg", "cfg"):
            raise ValueError("guidance_method must be apg|cfg")
        if not (1 <= self.steps <= 64):
            raise ValueError("steps out of sane range 1..64")

    @property
    def configured(self) -> bool:
        return bool(self.endpoint)


@dataclass(frozen=True)
class SynthesisRequest:
    text: str
    language: str                          # "pt-PT" | "en-NG" | ...
    voice_profile_id: Optional[str] = None  # None = default synthetic voice
    speaking_rate: float = 1.0

    def validated(self, max_chars: int) -> "SynthesisRequest":
        if not self.text.strip():
            raise ValueError("empty text — silence is not a concierge reply")
        if len(self.text) > max_chars:
            raise ValueError(f"text over {max_chars} chars — split utterances "
                             "instead of one long synthesis (latency + quality)")
        if not (0.5 <= self.speaking_rate <= 2.0):
            raise ValueError("speaking_rate out of 0.5..2.0")
        return self


@dataclass(frozen=True)
class SynthesisResult:
    request_id: str
    engine: str                            # "longcat-1b"|"longcat-3.5b"|"piper-fallback"|"none"
    audio_ref: Optional[str]               # object key / URL; None when engine="none"
    sample_rate: int
    duration_ms: int
    text: str                              # echoed — the EXACT spoken text
    provenance_note: str


def estimate_latent_frames(text: str, *, speaking_rate: float = 1.0) -> int:
    """Rough frame budget for the diffusion `duration` parameter.

    Heuristic, honestly labeled: ~14 chars/sec of speech for PT/EN at
    rate 1.0. Overshooting duration costs GPU time; undershooting clips
    the tail — so we bias ~15% long and let the VAE decoder pad silence,
    which the player trims. This is an ESTIMATE, and the serving server
    may refine it; callers must not treat it as exact.
    """
    chars_per_sec = 14.0 * speaking_rate
    seconds = max(1.0, len(text) / chars_per_sec) * 1.15
    return int(seconds * LATENT_FPS) + 1


# Injected probe: production wires an HTTP HEAD/health against the serving
# endpoint; tests wire fakes. Returns True when the GPU service answers.
HealthProbe = Callable[[str], bool]


def select_engine(config: Optional[LongCatTTSConfig], *,
                  probe: Optional[HealthProbe] = None,
                  piper_available: bool = True) -> str:
    """Decide the engine BEFORE synthesis so the utterance can be labeled
    correctly even in logs/metrics that never see the audio."""
    if config is not None and config.configured:
        if probe is None or probe(config.endpoint):
            return config.model_key
        # configured but unreachable — fall through with honesty
    if piper_available:
        return "piper-fallback"
    return "none"


# Injected synthesizer transport: (endpoint, payload) -> audio_ref, duration_ms
SynthTransport = Callable[[str, dict], "tuple[str, int]"]


def synthesize(request: SynthesisRequest, *,
               config: Optional[LongCatTTSConfig] = None,
               probe: Optional[HealthProbe] = None,
               transport: Optional[SynthTransport] = None,
               piper_transport: Optional[SynthTransport] = None,
               request_id: Optional[str] = None) -> SynthesisResult:
    """Synthesize one utterance with full provenance.

    No silent swaps: the result names the engine that actually ran.
    engine="none" means NO audio exists — the caller must render text
    (chat/voicemail transcript), and must not play a cached utterance
    from a different text.
    """
    rid = request_id or uuid4().hex
    engine = select_engine(config, probe=probe,
                           piper_available=piper_transport is not None)
    max_chars = config.max_text_chars if config else 500
    request.validated(max_chars)

    if engine in MODELS:
        assert config is not None and config.endpoint is not None
        payload = {
            "text": request.text,
            "language": request.language,
            "voice_profile_id": request.voice_profile_id,
            "duration": estimate_latent_frames(
                request.text, speaking_rate=request.speaking_rate),
            "steps": config.steps,
            "cfg_strength": config.cfg_strength,
            "guidance_method": config.guidance_method,
            "model_dir": MODELS[engine],
        }
        if transport is None:
            # configured but no way to call it — that's a wiring bug; say so.
            return SynthesisResult(rid, "none", None, SAMPLE_RATE, 0,
                                   request.text,
                                   "longcat configured but no transport wired")
        audio_ref, ms = transport(config.endpoint, payload)
        return SynthesisResult(rid, engine, audio_ref, SAMPLE_RATE, ms,
                               request.text, "synthesized by LongCat-AudioDiT")

    if engine == "piper-fallback":
        audio_ref, ms = piper_transport("piper", {  # type: ignore[misc]
            "text": request.text, "language": request.language})
        return SynthesisResult(rid, engine, audio_ref, SAMPLE_RATE, ms,
                               request.text,
                               "LongCat unavailable — piper fallback (LABELED)")

    return SynthesisResult(rid, "none", None, SAMPLE_RATE, 0, request.text,
                           "no TTS engine available — render text instead")
