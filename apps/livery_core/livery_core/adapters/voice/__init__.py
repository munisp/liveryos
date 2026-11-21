"""LongCat voice concierge (owner directive; meituan-longcat/LongCat-AudioDiT).

LongCat-AudioDiT is Meituan's MIT-licensed diffusion TTS (waveform-latent
DiT, 24 kHz, zero-shot voice cloning, 1B and 3.5B variants). In LiveryOS it
is the VOICE of the concierge: every spoken rider interaction (Pipecat
booking/status lines, voice-drop notifications) renders through it.

Honesty rules (same platform discipline):
- LongCat needs CUDA. If the GPU service isn't configured/reachable, the
  synthesizer returns a PIPER FALLBACK result with provenance labeled —
  utterances are never silently swapped, and never faked.
- Voice cloning processes biometric-adjacent data. A VoiceProfile without
  a recorded consent artifact REFUSES to build (GDPR Art. 9 / NDPA). No
  consent record, no cloned voice — the default synthetic voice is used.
- The concierge reads API numbers VERBATIM (fares, ETAs incl. eta_source)
  and never improvises figures; speech text carries the same provenance
  labels as the UI.
"""

from .longcat import (
    LongCatTTSConfig,
    SynthesisRequest,
    SynthesisResult,
    estimate_latent_frames,
    select_engine,
    synthesize,
)
from .profiles import (
    ConsentRequiredError,
    VoiceProfile,
    default_concierge_voice,
    register_profile,
)
from .concierge import (
    ConciergePersona,
    SpokenResponse,
    greet,
    persona_for_market,
    render_quote,
    render_status,
)

__all__ = [
    "LongCatTTSConfig", "SynthesisRequest", "SynthesisResult",
    "estimate_latent_frames", "select_engine", "synthesize",
    "ConsentRequiredError", "VoiceProfile", "default_concierge_voice",
    "register_profile",
    "ConciergePersona", "SpokenResponse", "greet", "persona_for_market",
    "render_quote", "render_status",
]
