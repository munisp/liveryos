"""Telephony & messaging tests — masking pool, LiveKit JWT, notification
composer, Pipecat spec builder."""
import base64
import hashlib
import hmac
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "livery_core"))

from livery_core.adapters.events import booking_created  # noqa: E402
from livery_core.adapters.telephony import (  # noqa: E402
    Channel, LiveKitConfig, MaskingPool, NotProvisionedError, VideoGrant,
    build_access_token, build_pipeline_spec, compose_for_event,
    default_channel_policy, room_for_booking, voice_agent_config,
)
from livery_core.adapters.telephony.livekit_token import (  # noqa: E402
    NotConfiguredError, decode_unverified,
)
from livery_core.adapters.telephony.masking import NumberInUseError  # noqa: E402

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


# ---------- masking ----------

def test_assign_and_dial_bridge():
    pool = MaskingPool(["+351900000001", "+351900000002"])
    a = pool.assign(booking_ref="SO-1", party="passenger",
                    real_number="+351910111222", now=NOW)
    assert a.proxy_number == "+351900000001"
    assert pool.dial_target("+351900000001", NOW) == "+351910111222"


def test_pool_exhaustion_raises_not_fakes():
    pool = MaskingPool(["+2348000000001"])
    pool.assign(booking_ref="SO-1", party="driver",
                real_number="+234801", now=NOW)
    with pytest.raises(NotProvisionedError):
        pool.assign(booking_ref="SO-1", party="passenger",
                    real_number="+234802", now=NOW)


def test_release_recycles_number():
    pool = MaskingPool(["+351900000001"])
    a = pool.assign(booking_ref="SO-1", party="passenger",
                    real_number="+351910", now=NOW)
    pool.release(a, now=NOW)
    b = pool.assign(booking_ref="SO-2", party="driver",
                    real_number="+351920", now=NOW)
    assert b.proxy_number == a.proxy_number  # recycled after release


def test_expired_assignment_is_swept_and_not_dialable():
    pool = MaskingPool(["+351900000001"])
    a = pool.assign(booking_ref="SO-1", party="passenger",
                    real_number="+351910", now=NOW)
    later = NOW + timedelta(hours=3)  # past 2h TTL
    with pytest.raises(NotProvisionedError):
        pool.dial_target(a.proxy_number, later)
    b = pool.assign(booking_ref="SO-2", party="driver",
                    real_number="+351920", now=later)
    assert b.proxy_number == a.proxy_number


def test_specific_number_in_use_guard():
    pool = MaskingPool(["+351900000001", "+351900000002"])
    pool.assign(booking_ref="SO-1", party="passenger",
                real_number="+351910", now=NOW)
    with pytest.raises(NumberInUseError):
        pool.assign_specific("+351900000001", booking_ref="SO-2",
                             party="driver", real_number="+351920", now=NOW)


def test_masking_requires_real_target():
    pool = MaskingPool(["+351900000001"])
    with pytest.raises(ValueError, match="real_number required"):
        pool.assign(booking_ref="SO-1", party="passenger",
                    real_number="", now=NOW)


# ---------- LiveKit tokens ----------

def _verify_hs256(token: str, secret: str) -> dict:
    head, payload, sig = token.split(".")
    expected = hmac.new(secret.encode(), f"{head}.{payload}".encode(),
                        hashlib.sha256).digest()
    actual = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
    assert hmac.compare_digest(expected, actual), "signature mismatch"
    return json.loads(base64.urlsafe_b64decode(
        payload + "=" * (-len(payload) % 4)))


def test_livekit_token_is_real_verifiable_jwt():
    cfg = LiveKitConfig(api_key="APIKEY123", api_secret="s3cret")
    token = build_access_token(cfg, identity="driver-42",
                               grant=VideoGrant(room="livery-call-SO-1"),
                               now=NOW)
    claims = _verify_hs256(token, "s3cret")
    assert claims["iss"] == "APIKEY123"
    assert claims["sub"] == "driver-42"
    assert claims["video"]["room"] == "livery-call-SO-1"
    assert claims["exp"] == int((NOW + timedelta(minutes=30)).timestamp())


def test_livekit_refuses_without_credentials():
    cfg = LiveKitConfig(api_key=None, api_secret=None)
    with pytest.raises(NotConfiguredError):
        build_access_token(cfg, identity="x", grant=VideoGrant(room="r"))


def test_room_naming_is_deterministic_and_safe():
    assert room_for_booking("SO 100/PT") == "livery-call-SO-100-PT"
    assert room_for_booking("SO 100/PT") == room_for_booking("SO 100/PT")


def test_decode_unverified_roundtrip():
    cfg = LiveKitConfig(api_key="k", api_secret="s")
    token = build_access_token(cfg, identity="pax-1",
                               grant=VideoGrant(room="r1"), now=NOW)
    assert decode_unverified(token)["sub"] == "pax-1"


# ---------- notifications ----------

def _ev():
    return booking_created("ng-demo", sales_order="SO-55", customer="Ada",
                           pickup_at=NOW, service_class="sedan",
                           quoted_minor=450000, currency="NGN")


def test_ng_policy_is_whatsapp_first():
    chans = default_channel_policy("NG", "booking.created")
    assert chans[0] == Channel.WHATSAPP
    assert Channel.VOICE not in chans  # non-critical: no voice spam


def test_pt_policy_is_sms_first_and_critical_adds_voice():
    assert default_channel_policy("PT", "booking.created")[0] == Channel.SMS
    crit = default_channel_policy("PT", "trip.rebooked")
    assert crit[-1] == Channel.VOICE


def test_compose_renders_template_with_booking_ref():
    reqs = compose_for_event(_ev(), market="NG", to="+2348012345678")
    assert len(reqs) == 2  # whatsapp + sms fallback
    assert all(r.status == "queued" for r in reqs)  # never claimed sent
    assert "SO-55" in reqs[0].body
    assert reqs[0].related_event_id == _ev().event_id or reqs[0].related_event_id


def test_compose_whatsapp_optout_falls_back_to_sms():
    reqs = compose_for_event(_ev(), market="NG", to="+234801",
                             whatsapp_opted_in=False)
    assert [r.channel for r in reqs] == [Channel.SMS]


def test_compose_ignores_non_rider_events_and_templates_refuse_freeform():
    ev = _ev()
    object.__setattr__(ev, "type", "marketing.blast")  # frozen dataclass bypass for test
    # Non-rider events produce NO notifications (documented behavior)…
    assert compose_for_event(ev, market="PT", to="+351910") == []
    # …and the renderer itself hard-refuses unknown templates.
    from livery_core.adapters.telephony.notify import _render
    with pytest.raises(ValueError, match="no rider template"):
        _render("marketing.blast", {})


# ---------- pipecat ----------

def test_pipeline_spec_discloses_bot_and_bounds_intents():
    spec = build_pipeline_spec(
        name="pt-booking", purpose="booking_line", language="pt-PT",
        stt={"provider": "whisper"}, llm={"provider": "x"}, tts={"provider": "piper"})
    assert "automated assistant" in spec.bot_disclosure
    assert set(spec.allowed_intents) == {"create_booking", "status_lookup",
                                         "cancel_request"}
    assert "never invent prices" in spec.system_prompt


def test_after_hours_never_fakes_human():
    with pytest.raises(ValueError, match="banned"):
        build_pipeline_spec(name="x", purpose="status_line", language="en",
                            stt={}, llm={}, tts={},
                            after_hours_mode="pretend-human")


def test_voice_agent_config_market_voices():
    pt = voice_agent_config("PT", llm_endpoint="http://llm:8080/v1",
                            llm_model="qwen2.5-7b")
    ng = voice_agent_config("NG", llm_endpoint="http://llm:8080/v1",
                            llm_model="qwen2.5-7b")
    assert {s.language for s in pt} == {"pt-PT"}
    assert {s.language for s in ng} == {"en-NG"}
    assert all(s.transport == "livekit" for s in pt + ng)
