"""LongCat voice concierge tests — engine selection honesty, consent gating,
verbatim rendering, fallback provenance."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "livery_core"))

from livery_core.adapters.voice import (  # noqa: E402
    ConsentRequiredError, LongCatTTSConfig, SynthesisRequest, estimate_latent_frames,
    greet, persona_for_market, register_profile, render_quote, render_status,
    select_engine, synthesize,
)
from livery_core.adapters.voice.concierge import render_escalation  # noqa: E402
from livery_core.adapters.voice.profiles import (  # noqa: E402
    ConsentScopeError, check_scope, default_concierge_voice, revoke,
)
from livery_core.adapters.telephony.pipecat_bot import (  # noqa: E402
    longcat_tts_spec, voice_agent_config,
)


def cfg(endpoint="http://longcat-tts:8100"):
    return LongCatTTSConfig(endpoint=endpoint)


REQ = SynthesisRequest(text="Your driver arrives in about 12 minutes, estimate.",
                       language="pt-PT")


# ---------- engine selection honesty ----------

def test_longcat_selected_when_configured_and_healthy():
    assert select_engine(cfg(), probe=lambda ep: True) == "longcat-1b"


def test_configured_but_unreachable_falls_back_labeled():
    assert select_engine(cfg(), probe=lambda ep: False) == "piper-fallback"


def test_unconfigured_is_piper_or_none_never_longcat():
    assert select_engine(None) == "piper-fallback"
    assert select_engine(None, piper_available=False) == "none"
    assert select_engine(cfg(endpoint=None)) == "piper-fallback"


def test_synthesize_labels_engine_and_echoes_exact_text():
    res = synthesize(REQ, config=cfg(), probe=lambda ep: True,
                     transport=lambda ep, p: ("s3://u/1.wav", 2400))
    assert res.engine == "longcat-1b"
    assert res.text == REQ.text  # exact spoken text, echoed for audit
    assert "LongCat" in res.provenance_note


def test_piper_fallback_is_explicitly_labeled():
    res = synthesize(REQ, config=cfg(), probe=lambda ep: False,
                     piper_transport=lambda ep, p: ("s3://u/2.wav", 2500))
    assert res.engine == "piper-fallback"
    assert "fallback" in res.provenance_note.lower()


def test_none_engine_means_no_audio_exists():
    res = synthesize(REQ, config=None)
    assert res.engine == "none" and res.audio_ref is None


def test_configured_without_transport_is_a_wiring_bug_not_fake_audio():
    res = synthesize(REQ, config=cfg(), probe=lambda ep: True, transport=None)
    assert res.engine == "none"
    assert "no transport wired" in res.provenance_note


def test_empty_text_rejected_silence_is_not_a_reply():
    with pytest.raises(ValueError, match="silence"):
        synthesize(SynthesisRequest(text="  ", language="en-NG"), config=cfg())


def test_oversized_text_rejected():
    with pytest.raises(ValueError, match="split utterances"):
        synthesize(SynthesisRequest(text="x" * 501, language="en-NG"),
                   config=cfg())


def test_duration_estimate_scales_and_biases_long():
    short = estimate_latent_frames("Hi.")
    long = estimate_latent_frames("word " * 100)
    assert long > short
    # long enough to escape the 1-second floor so the rate effect shows
    utterance = "your driver is on the way to the pickup point now "
    assert estimate_latent_frames(utterance, speaking_rate=2.0) < \
        estimate_latent_frames(utterance, speaking_rate=1.0)


def test_config_validation():
    with pytest.raises(ValueError, match="unknown model"):
        LongCatTTSConfig(model_key="longcat-900b")
    with pytest.raises(ValueError, match="apg|cfg"):
        LongCatTTSConfig(guidance_method=" vibes")


# ---------- consent gating (GDPR Art.9 / NDPA) ----------

def test_cloned_voice_without_consent_refused():
    with pytest.raises(ConsentRequiredError):
        register_profile(profile_id="brand-pt-01", label="Ana", language="pt-PT",
                         cloned=True, prompt_audio_ref="s3://p.wav",
                         prompt_text="Olá.")


def test_cloned_voice_needs_scope_and_transcript():
    with pytest.raises(ConsentRequiredError, match="scope"):
        register_profile(profile_id="b", label="b", language="en-NG",
                         cloned=True, prompt_audio_ref="s3://p.wav",
                         prompt_text="Hello.",
                         consent_artifact="consent://r/1")
    with pytest.raises(ValueError, match="prompt_text"):
        register_profile(profile_id="b", label="b", language="en-NG",
                         cloned=True, prompt_audio_ref="s3://p.wav",
                         consent_artifact="consent://r/1",
                         consent_scope=("tenant:*",))


def test_valid_cloned_profile_and_scope_enforcement():
    p = register_profile(profile_id="brand-ng-01", label="Adaeze",
                         language="en-NG", cloned=True,
                         prompt_audio_ref="s3://prompts/adaeze.wav",
                         prompt_text="Good morning, this is LiveryOS.",
                         consent_artifact="consent://releases/2026-11/va-014",
                         consent_scope=("tenant:lagos-*", "purpose:concierge"))
    check_scope(p, tenant="lagos-*", purpose="concierge")
    with pytest.raises(ConsentScopeError):
        check_scope(p, tenant="lagos-*", purpose="ads")  # not consented


def test_revoked_voice_unusable_and_prompt_ref_stripped():
    p = register_profile(profile_id="brand-ng-01", label="Adaeze",
                         language="en-NG", cloned=True,
                         prompt_audio_ref="s3://prompts/adaeze.wav",
                         prompt_text="t", consent_artifact="consent://r/1",
                         consent_scope=("tenant:*", "purpose:*"))
    r = revoke(p)
    assert not r.usable()
    assert r.prompt_audio_ref is None  # not just hidden — dereferenced
    with pytest.raises(ConsentRequiredError, match="revoked"):
        check_scope(r, tenant="t", purpose="concierge")


def test_default_voice_is_always_lawful_synthetic():
    v = default_concierge_voice("pt-PT")
    assert not v.cloned and v.usable()
    check_scope(v, tenant="anything", purpose="anything")


# ---------- concierge rendering (verbatim numbers) ----------

def test_personas_exist_for_both_markets_and_disclose_bot():
    for market, lang in (("PT", "pt-PT"), ("NG", "en-NG")):
        p = persona_for_market(market)
        assert p.language == lang
        assert "assist" in p.disclosure.lower() or "assistente" in p.disclosure.lower()
        g = greet(p)
        assert p.disclosure in g.text  # first contact always discloses


def test_unknown_market_persona_refused():
    with pytest.raises(ValueError, match="never ad-libs"):
        persona_for_market("US")


def test_quote_rendered_verbatim_with_spoken_price():
    p = persona_for_market("NG")
    r = render_quote(p, quoted_minor=450000, currency="NGN",
                     service_class="executive", pickup_at_human="3:30 PM",
                     engine_version="1.0.0")
    assert "4,500.00 naira" in r.text
    assert "executive" in r.text and "3:30 PM" in r.text
    assert "4,500.00 naira" in r.verbatim_fields


def test_unknown_currency_spoken_name_never_guessed():
    with pytest.raises(ValueError, match="never guess"):
        render_quote(persona_for_market("PT"), quoted_minor=100,
                     currency="USD", service_class="business",
                     pickup_at_human="15:30", engine_version="1.0.0")


def test_eta_source_word_is_spoken_not_fine_print():
    p = persona_for_market("PT")
    r = render_status(p, status="EN_ROUTE", eta_minutes=12,
                      eta_source="estimate", driver_name="Carlos")
    assert "about 12 minutes, estimate" in r.text
    assert "Carlos" in r.text


def test_no_eta_is_said_honestly():
    r = render_status(persona_for_market("NG"), status="ASSIGNED",
                      eta_minutes=None, eta_source="none", driver_name=None)
    assert "don't have a reliable arrival estimate" in r.text


def test_after_hours_never_promises_a_human_who_isnt_rostered():
    r = render_escalation(persona_for_market("NG"), after_hours=True,
                          human_rostered=False)
    assert "not available" in r.text
    assert "call you back" in r.text


# ---------- pipecat integration ----------

def test_longcat_spec_primary_with_piper_fallback():
    spec = longcat_tts_spec(endpoint="http://longcat-tts:8100",
                            voice_profile_id="brand-pt-01")
    assert spec["provider"] == "longcat-audiodit"
    assert spec["fallback"]["provider"] == "piper"
    assert spec["provenance_per_utterance"] is True
    assert spec["sample_rate"] == 24000


def test_longcat_spec_disabled_is_honest_piper_primary():
    spec = longcat_tts_spec(endpoint=None)
    assert spec["provider"] == "piper"
    assert "not configured" in spec["note"]


def test_voice_agent_config_uses_longcat_when_endpoint_given():
    specs = voice_agent_config("NG", llm_endpoint="http://llm/v1",
                               llm_model="qwen", longcat_endpoint="http://lc:8100")
    assert all(s.tts["provider"] == "longcat-audiodit" for s in specs)
    specs_off = voice_agent_config("PT", llm_endpoint="http://llm/v1",
                                   llm_model="qwen")
    assert all(s.tts["provider"] == "piper" for s in specs_off)
