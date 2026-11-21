"""Voice profiles — zero-shot cloning with consent as a HARD precondition.

LongCat-AudioDiT clones a voice from (prompt_audio + prompt_text). That is
processing of a voice — biometric-adjacent personal data under GDPR Art. 9
and Nigeria's NDPA. Therefore:

- A cloned VoiceProfile can only be REGISTERED with a consent artifact
  reference (signed release / recorded agreement id). No artifact →
  ConsentRequiredError. There is no "skip for now" path.
- Consent scope is recorded (which tenants/purposes may use the voice).
  Using a profile outside its scope raises.
- Revocation tombstones the profile: synthesize calls with a revoked
  profile fall back to the default synthetic voice with provenance noting
  the revocation — the revoked voice's prompt audio reference is deleted
  from the profile, never just "hidden".
- The platform DEFAULT voice is fully synthetic (no human model), so the
  concierge always has a lawful voice even with zero consents on file.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple


class ConsentRequiredError(PermissionError):
    """Attempted to register/use a cloned voice without consent on file."""


class ConsentScopeError(PermissionError):
    """Profile used outside its consented scope (tenant/purpose)."""


@dataclass(frozen=True)
class VoiceProfile:
    profile_id: str
    label: str
    language: str
    cloned: bool
    prompt_audio_ref: Optional[str] = None    # object key to prompt.wav
    prompt_text: Optional[str] = None         # transcript of prompt audio
    consent_artifact: Optional[str] = None    # e.g. "consent://releases/2026-11/va-014"
    consent_scope: Tuple[str, ...] = ()       # e.g. ("tenant:*", "purpose:concierge")
    registered_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc))
    revoked_at: Optional[datetime] = None

    def usable(self) -> bool:
        return self.revoked_at is None


def register_profile(*, profile_id: str, label: str, language: str,
                     cloned: bool, prompt_audio_ref: Optional[str] = None,
                     prompt_text: Optional[str] = None,
                     consent_artifact: Optional[str] = None,
                     consent_scope: Tuple[str, ...] = ()) -> VoiceProfile:
    """Register a voice. Cloned voices MUST carry consent + scope + prompt
    transcript (the model needs the transcript; the LAW needs the consent)."""
    if cloned:
        if not consent_artifact:
            raise ConsentRequiredError(
                f"cloned voice {profile_id!r} has no consent artifact — "
                "GDPR Art. 9 / NDPA: voice cloning needs explicit recorded "
                "consent BEFORE registration")
        if not consent_scope:
            raise ConsentRequiredError(
                "consent without scope is not consent — record which "
                "tenants/purposes this voice may serve")
        if not prompt_audio_ref or not prompt_text:
            raise ValueError("cloning requires prompt_audio_ref AND its "
                             "verbatim prompt_text (LongCat zero-shot input)")
    else:
        if prompt_audio_ref or consent_artifact:
            raise ValueError("synthetic profiles carry no prompt audio or "
                             "consent artifacts — mark cloned=True if this "
                             "voice models a real person")
    return VoiceProfile(profile_id, label, language, cloned,
                        prompt_audio_ref, prompt_text,
                        consent_artifact, consent_scope)


def check_scope(profile: VoiceProfile, *, tenant: str, purpose: str) -> None:
    if not profile.cloned:
        return
    if not profile.usable():
        raise ConsentRequiredError(f"voice {profile.profile_id} was revoked")
    tenant_ok = any(s in ("tenant:*", f"tenant:{tenant}")
                    for s in profile.consent_scope)
    purpose_ok = any(s in ("purpose:*", f"purpose:{purpose}")
                     for s in profile.consent_scope)
    if not (tenant_ok and purpose_ok):
        raise ConsentScopeError(
            f"voice {profile.profile_id} consent scope "
            f"{profile.consent_scope} does not cover "
            f"tenant:{tenant} purpose:{purpose}")


def revoke(profile: VoiceProfile) -> VoiceProfile:
    """Tombstone + strip the prompt reference. The audio object itself is
    deleted by the storage adapter; here the profile becomes unusable and
    stops pointing at it."""
    return replace(profile, revoked_at=datetime.now(timezone.utc),
                   prompt_audio_ref=None)


def default_concierge_voice(language: str) -> VoiceProfile:
    """The always-lawful fallback: fully synthetic, no human model."""
    return VoiceProfile(
        profile_id=f"synthetic-concierge-{language.lower()}",
        label=f"LiveryOS concierge ({language})",
        language=language, cloned=False,
        consent_scope=("tenant:*", "purpose:*"),
    )
