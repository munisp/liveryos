"""Payments breadth tests — currency fencing, intent lifecycle, posting,
commission automation. Transports are fakes; no network, ever."""
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "livery_core"))

from livery_core.adapters.payments import (  # noqa: E402
    CurrencyFenceError, GatewayError, IntentState, IntentStore,
    NotConfiguredError, PaystackGateway, StripeGateway, gateway_for_currency,
)
from livery_core.adapters.payments import intent as intent_mod  # noqa: E402
from livery_core.adapters.payments import posting  # noqa: E402
from livery_core.affiliates.engine import Partner  # noqa: E402


# ---------- fake transports ----------

def stripe_ok(status=200, body=None):
    def transport(method, path, payload, headers, timeout):
        assert headers["Authorization"].startswith("Bearer ")
        assert "Idempotency-Key" in headers
        if body is not None:
            return status, body
        if path.endswith("/capture"):
            return 200, {"id": "pi_123", "status": "succeeded", "currency": "eur"}
        if path == "/v1/refunds":
            return 200, {"id": "re_123", "status": "succeeded"}
        return 200, {"id": "pi_123", "status": "requires_capture",
                     "currency": payload["currency"]}
    return transport


def paystack_ok(body=None):
    def transport(method, path, payload, headers, timeout):
        if body is not None:
            return 200, body
        return 200, {"status": True, "data": {
            "reference": payload.get("reference", "ref_1"),
            "status": "success", "currency": payload.get("currency", "NGN")}}
    return transport


# ---------- gateway fencing ----------

def test_currency_fence_stripe_rejects_ngn():
    g = StripeGateway(secret_key="sk", transport=stripe_ok())
    with pytest.raises(CurrencyFenceError):
        g.authorize(amount_minor=1000, currency="NGN", reference="SO-1",
                    customer_ref="cust", card_token="pm_1",
                    idempotency_key="k1")


def test_currency_fence_paystack_rejects_eur():
    g = PaystackGateway(secret_key="sk", transport=paystack_ok())
    with pytest.raises(CurrencyFenceError):
        g.authorize(amount_minor=1000, currency="EUR", reference="SO-1",
                    customer_ref="cust", card_token="auth_code",
                    idempotency_key="k2")


def test_gateway_routing_by_currency():
    s = StripeGateway(secret_key="sk", transport=stripe_ok())
    p = PaystackGateway(secret_key="sk", transport=paystack_ok())
    assert gateway_for_currency("EUR", stripe=s, paystack=p) is s
    assert gateway_for_currency("ngn", stripe=s, paystack=p) is p
    with pytest.raises(CurrencyFenceError):
        gateway_for_currency("USD", stripe=s, paystack=p)


def test_unconfigured_gateway_refuses_to_fake_charge():
    g = StripeGateway(secret_key=None, transport=None)
    with pytest.raises(NotConfiguredError):
        g.authorize(amount_minor=1000, currency="EUR", reference="SO-1",
                    customer_ref="cust", card_token="pm_1",
                    idempotency_key="k3")


def test_currency_echo_mismatch_is_hard_error():
    g = StripeGateway(secret_key="sk", transport=stripe_ok(
        body={"id": "pi_9", "status": "requires_capture", "currency": "usd"}))
    with pytest.raises(GatewayError, match="currency echo mismatch"):
        g.authorize(amount_minor=1000, currency="EUR", reference="SO-1",
                    customer_ref="cust", card_token="pm_1",
                    idempotency_key="k4")


def test_provider_error_surfaces_reason():
    g = StripeGateway(secret_key="sk", transport=stripe_ok(
        status=402, body={"error": {"type": "card_error",
                                    "message": "card declined"}}))
    res = g.authorize(amount_minor=1000, currency="EUR", reference="SO-1",
                      customer_ref="cust", card_token="pm_1",
                      idempotency_key="k5")
    assert not res.ok and res.failure_reason == "card declined"


def test_paystack_refuses_raw_card_tokenization():
    g = PaystackGateway(secret_key="sk", transport=paystack_ok())
    with pytest.raises(GatewayError, match="client-side"):
        g.tokenize(card_payload={"number": "4111..."}, idempotency_key="k6")


# ---------- intent lifecycle ----------

def _authorized_eur_intent():
    store = IntentStore()
    gw = StripeGateway(secret_key="sk", transport=stripe_ok())
    intent = intent_mod.authorize_intent(
        store, gw, tenant="pt-demo", sales_order="SO-100",
        amount_minor=5000, currency="EUR", customer_ref="cu_1",
        card_token="pm_1", idempotency_key="idem-1")
    return store, gw, intent


def test_authorize_capture_happy_path():
    store, gw, intent = _authorized_eur_intent()
    assert intent.state == IntentState.AUTHORIZED
    assert intent.authorization_ref == "pi_123"
    done = intent_mod.capture_on_completion(
        store, gw, intent_id=intent.intent_id, final_amount_minor=4800,
        idempotency_key="idem-1")
    assert done.state == IntentState.CAPTURED
    assert done.captured_minor == 4800  # under-capture allowed


def test_capture_never_exceeds_authorization():
    store, gw, intent = _authorized_eur_intent()
    with pytest.raises(GatewayError, match="exceeds authorization"):
        intent_mod.capture_on_completion(
            store, gw, intent_id=intent.intent_id, final_amount_minor=5001,
            idempotency_key="idem-1")


def test_idempotent_replay_returns_same_intent():
    store, gw, intent = _authorized_eur_intent()
    again = intent_mod.authorize_intent(
        store, gw, tenant="pt-demo", sales_order="SO-100",
        amount_minor=5000, currency="EUR", customer_ref="cu_1",
        card_token="pm_1", idempotency_key="idem-1")
    assert again.intent_id == intent.intent_id
    assert again.state == IntentState.AUTHORIZED  # not double-authorized


def test_illegal_transition_raises():
    store, gw, intent = _authorized_eur_intent()
    with pytest.raises(GatewayError, match="illegal intent transition"):
        intent.transition(IntentState.REFUNDED)


def test_release_then_capture_raises():
    store, gw, intent = _authorized_eur_intent()
    intent_mod.release_authorization(store, intent_id=intent.intent_id)
    with pytest.raises(GatewayError):
        intent_mod.capture_on_completion(
            store, gw, intent_id=intent.intent_id, final_amount_minor=100,
            idempotency_key="idem-1")


def test_refund_after_capture():
    store, gw, intent = _authorized_eur_intent()
    done = intent_mod.capture_on_completion(
        store, gw, intent_id=intent.intent_id, final_amount_minor=5000,
        idempotency_key="idem-1")
    refunded = intent_mod.refund_capture(
        store, gw, intent_id=done.intent_id, amount_minor=2000,
        idempotency_key="idem-1")
    assert refunded.state == IntentState.REFUNDED
    assert refunded.refunded_minor == 2000


# ---------- posting + commission automation ----------

def test_posting_refuses_authorized_only():
    store, gw, intent = _authorized_eur_intent()
    with pytest.raises(ValueError, match="only CAPTURED"):
        posting.post_payment_to_invoice(tenant="pt-demo",
                                        sales_invoice="INV-1", intent=intent)


def test_posting_builds_payment_entry_payload():
    store, gw, intent = _authorized_eur_intent()
    done = intent_mod.capture_on_completion(
        store, gw, intent_id=intent.intent_id, final_amount_minor=5000,
        idempotency_key="idem-1")
    p, ev = posting.post_payment_to_invoice(
        tenant="pt-demo", sales_invoice="INV-1", intent=done)
    assert p.mode_of_payment == "Stripe"
    assert p.paid_minor == 5000 and p.currency == "EUR"
    assert ev.type == "invoice.paid"
    assert ev.payload["idempotency_key"] == "idem-1:post"


def test_commission_split_sums_to_gross():
    store, gw, intent = _authorized_eur_intent()
    done = intent_mod.capture_on_completion(
        store, gw, intent_id=intent.intent_id, final_amount_minor=4999,
        idempotency_key="idem-1")
    partner = Partner(partner_ref="P-001", direction="both",
                      split_pct=Decimal("30"), active=True)
    c, ev = posting.commission_for_captured_payment(
        tenant="pt-demo", intent=done, partner=partner)
    assert c.partner_minor + c.operator_minor == 4999  # zero penny loss
    assert c.partner_minor == 1500                      # 30% of 4999 → 1500 HALF_UP
    assert ev.type == "affiliate.commission_settled"


def test_commission_quote_partner_is_honest_pending():
    store, gw, intent = _authorized_eur_intent()
    done = intent_mod.capture_on_completion(
        store, gw, intent_id=intent.intent_id, final_amount_minor=5000,
        idempotency_key="idem-1")
    partner = Partner(partner_ref="P-002", direction="both",
                      split_pct=None, active=True)
    c, ev = posting.commission_for_captured_payment(
        tenant="pt-demo", intent=done, partner=partner)
    assert ev.type == "affiliate.commission_pending_quote"
    assert "per-booking quote" in ev.payload["reason"]
