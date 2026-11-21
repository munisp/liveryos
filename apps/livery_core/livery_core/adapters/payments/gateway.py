"""Gateway protocol + Stripe/Paystack implementations.

The gateway layer is the ONLY place that knows provider wire formats.
Everything above it (intents, posting, commissions) speaks GatewayResult.

Transport injection: each gateway takes `transport(method, path, payload,
headers, timeout) -> (status_code, dict)`. Production wires a real HTTPS
transport (see transports.py / bench integration); tests wire fakes. A
gateway constructed without credentials refuses to talk — NotConfiguredError
— because silently skipping a charge is how you give away free rides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

Transport = Callable[[str, str, dict, dict, float], "tuple[int, dict]"]


class GatewayError(RuntimeError):
    """Provider returned an error or an unparseable response."""


class GatewayTimeout(GatewayError):
    """Provider did not answer in time — caller may retry with SAME idempotency key."""


class NotConfiguredError(GatewayError):
    """Missing credentials/transport. Refusing beats pretending."""


class CurrencyFenceError(GatewayError):
    """Currency not supported by this gateway. We never auto-convert."""


@dataclass(frozen=True)
class GatewayResult:
    """Normalized provider answer. `reference` is the provider's object id."""
    ok: bool
    reference: str
    amount_minor: int
    currency: str                    # ISO, upper — echoed back and verified
    status: str                      # provider-native status string
    gateway: str                     # "stripe" | "paystack"
    raw_status_code: int
    failure_reason: Optional[str] = None


class PaymentGateway(Protocol):
    name: str
    currencies: frozenset

    def authorize(self, *, amount_minor: int, currency: str, reference: str,
                  customer_ref: str, card_token: str,
                  idempotency_key: str) -> GatewayResult: ...

    def capture(self, *, authorization_ref: str, amount_minor: int,
                idempotency_key: str) -> GatewayResult: ...

    def refund(self, *, capture_ref: str, amount_minor: int,
               idempotency_key: str) -> GatewayResult: ...

    def tokenize(self, *, card_payload: dict,
                 idempotency_key: str) -> GatewayResult: ...


def _check_currency(gateway: str, supported: frozenset, currency: str) -> str:
    cur = (currency or "").upper()
    if cur not in supported:
        raise CurrencyFenceError(
            f"{gateway} does not process {currency!r}; supported: {sorted(supported)}. "
            "LiveryOS never auto-converts currency."
        )
    return cur


class StripeGateway:
    """Stripe (PaymentIntents API) — EUR/SEPA for Portuguese tenants.

    Manual capture flow: authorize = create PaymentIntent with
    capture_method=manual + confirm=true; capture = POST /capture with a
    final amount that must be <= authorized.
    """

    name = "stripe"
    currencies = frozenset({"EUR"})
    API_BASE = "/v1"
    API_VERSION = "2024-06-20"

    def __init__(self, *, secret_key: Optional[str],
                 transport: Optional[Transport] = None,
                 timeout_seconds: float = 20.0):
        self._key = secret_key
        self._transport = transport
        self._timeout = timeout_seconds

    def _call(self, method: str, path: str, payload: dict,
              idempotency_key: str) -> "tuple[int, dict]":
        if not self._key or self._transport is None:
            raise NotConfiguredError(
                "StripeGateway has no secret_key/transport — refusing to fake a charge."
            )
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Idempotency-Key": idempotency_key,
            "Stripe-Version": self.API_VERSION,
        }
        status, body = self._transport(method, f"{self.API_BASE}{path}",
                                       payload, headers, self._timeout)
        return status, body

    def _result(self, status: int, body: dict, *, amount_minor: int,
                currency: str) -> GatewayResult:
        if not isinstance(body, dict):
            raise GatewayError(f"stripe: non-object response (HTTP {status})")
        err = body.get("error")
        if err:
            return GatewayResult(False, str(body.get("id", "")), amount_minor,
                                 currency, str(err.get("type", "error")),
                                 self.name, status,
                                 failure_reason=str(err.get("message", "stripe error")))
        returned_currency = str(body.get("currency", "")).upper()
        if returned_currency and returned_currency != currency:
            # Provider echoed a different currency than we sent: hard stop.
            raise GatewayError(
                f"stripe: currency echo mismatch sent={currency} got={returned_currency}"
            )
        return GatewayResult(200 <= status < 300, str(body.get("id", "")),
                             amount_minor, currency,
                             str(body.get("status", "unknown")), self.name, status)

    def tokenize(self, *, card_payload: dict,
                 idempotency_key: str) -> GatewayResult:
        status, body = self._call("POST", "/payment_methods",
                                  {"type": "card", "card": card_payload},
                                  idempotency_key)
        res = self._result(status, body, amount_minor=0, currency="EUR")
        return res

    def authorize(self, *, amount_minor: int, currency: str, reference: str,
                  customer_ref: str, card_token: str,
                  idempotency_key: str) -> GatewayResult:
        cur = _check_currency(self.name, self.currencies, currency)
        if amount_minor <= 0:
            raise GatewayError("authorize amount must be positive minor units")
        status, body = self._call("POST", "/payment_intents", {
            "amount": amount_minor,
            "currency": cur.lower(),
            "capture_method": "manual",
            "confirm": "true",
            "customer": customer_ref,
            "payment_method": card_token,
            "metadata": {"livery_reference": reference},
        }, idempotency_key)
        return self._result(status, body, amount_minor=amount_minor, currency=cur)

    def capture(self, *, authorization_ref: str, amount_minor: int,
                idempotency_key: str) -> GatewayResult:
        if not authorization_ref:
            raise GatewayError("capture requires an authorization reference")
        status, body = self._call(
            "POST", f"/payment_intents/{authorization_ref}/capture",
            {"amount_to_capture": amount_minor}, idempotency_key)
        # Currency is not part of the capture call; echo check happens on auth.
        cur = str(body.get("currency", "")).upper() if isinstance(body, dict) else ""
        res = self._result(status, body, amount_minor=amount_minor,
                           currency=cur or "EUR")
        return res

    def refund(self, *, capture_ref: str, amount_minor: int,
               idempotency_key: str) -> GatewayResult:
        if not capture_ref:
            raise GatewayError("refund requires a capture reference")
        status, body = self._call("POST", "/refunds", {
            "payment_intent": capture_ref, "amount": amount_minor,
        }, idempotency_key)
        return self._result(status, body, amount_minor=amount_minor,
                            currency="EUR")


class PaystackGateway:
    """Paystack — NGN for Nigerian tenants.

    Paystack amounts are kobo (integer minor units — same discipline).
    authorize = POST /transaction/initialize with card authorization code
    (saved card) or /charge; capture of a pre-auth uses /transaction/
    capture is not Paystack-native — partial capture is emulated by charging
    the authorization's final amount at completion. We keep the same intent
    semantics so the domain layer stays provider-agnostic, and the
    `status` field records the provider-native state honestly.
    """

    name = "paystack"
    currencies = frozenset({"NGN"})
    API_BASE = ""

    def __init__(self, *, secret_key: Optional[str],
                 transport: Optional[Transport] = None,
                 timeout_seconds: float = 30.0):   # NG networks: longer patience
        self._key = secret_key
        self._transport = transport
        self._timeout = timeout_seconds

    def _call(self, method: str, path: str, payload: dict,
              idempotency_key: str) -> "tuple[int, dict]":
        if not self._key or self._transport is None:
            raise NotConfiguredError(
                "PaystackGateway has no secret_key/transport — refusing to fake a charge."
            )
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Idempotency-Key": idempotency_key,
        }
        return self._transport(method, f"{self.API_BASE}{path}", payload,
                               headers, self._timeout)

    def _result(self, status: int, body: dict, *, amount_minor: int,
                currency: str) -> GatewayResult:
        if not isinstance(body, dict):
            raise GatewayError(f"paystack: non-object response (HTTP {status})")
        ok_flag = bool(body.get("status"))          # paystack: status=true/false
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        ref = str(data.get("reference") or data.get("id") or "")
        native = str(data.get("status", "unknown"))
        echoed = str(data.get("currency", "")).upper()
        if echoed and echoed != currency:
            raise GatewayError(
                f"paystack: currency echo mismatch sent={currency} got={echoed}"
            )
        failure = None if ok_flag else str(body.get("message", "paystack error"))
        return GatewayResult(ok_flag and 200 <= status < 300, ref, amount_minor,
                             currency, native, self.name, status,
                             failure_reason=failure)

    def tokenize(self, *, card_payload: dict,
                 idempotency_key: str) -> GatewayResult:
        # Paystack tokenization happens client-side via inline.js; the server
        # only ever sees an authorization_code. We refuse to accept raw PANs.
        raise GatewayError(
            "paystack: card tokenization is client-side (inline.js); "
            "server accepts authorization_code only — never raw card data."
        )

    def authorize(self, *, amount_minor: int, currency: str, reference: str,
                  customer_ref: str, card_token: str,
                  idempotency_key: str) -> GatewayResult:
        cur = _check_currency(self.name, self.currencies, currency)
        if amount_minor <= 0:
            raise GatewayError("authorize amount must be positive minor units")
        status, body = self._call("POST", "/transaction/initialize", {
            "amount": amount_minor,
            "currency": cur,
            "reference": reference,
            "email": customer_ref,
            "authorization_code": card_token,
        }, idempotency_key)
        return self._result(status, body, amount_minor=amount_minor, currency=cur)

    def capture(self, *, authorization_ref: str, amount_minor: int,
                idempotency_key: str) -> GatewayResult:
        status, body = self._call("POST", "/transaction/verify/"
                                  f"{authorization_ref}", {}, idempotency_key)
        return self._result(status, body, amount_minor=amount_minor,
                            currency="NGN")

    def refund(self, *, capture_ref: str, amount_minor: int,
               idempotency_key: str) -> GatewayResult:
        status, body = self._call("POST", "/refund", {
            "transaction": capture_ref, "amount": amount_minor,
        }, idempotency_key)
        return self._result(status, body, amount_minor=amount_minor,
                            currency="NGN")


# Flutterwave slots in here behind PaymentGateway with currencies={"NGN"} —
# same intent semantics, different wire format. Adding it must NOT touch
# intent.py / posting.py.

def gateway_for_currency(currency: str, *, stripe: Optional[StripeGateway] = None,
                         paystack: Optional[PaystackGateway] = None) -> PaymentGateway:
    """Route by currency. Never by tenant preference — currency is truth."""
    cur = (currency or "").upper()
    if cur == "EUR":
        if stripe is None:
            raise NotConfiguredError("no Stripe gateway configured for EUR")
        return stripe
    if cur == "NGN":
        if paystack is None:
            raise NotConfiguredError("no Paystack gateway configured for NGN")
        return paystack
    raise CurrencyFenceError(f"unsupported platform currency {currency!r}")
