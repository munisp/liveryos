"""Payment breadth (report Ch. 6 §6.4; GA parity gap (c)).

GA ships eight gateways plus EBizCharge invoice posting and affiliate
commission automation. LiveryOS deliberately ships FEWER gateways — the
two that cover our dual market honestly:

- Stripe     → EUR / SEPA (Portugal, EU tenants)
- Paystack   → NGN (Nigeria)  — Flutterwave slots behind the same protocol

Hard rules (non-negotiable, inherited from the platform):
- Money is integer minor units. Always. No floats anywhere near money.
- EUR and NGN are NEVER blended, converted, or co-mingled. A currency
  that doesn't match the gateway raises CurrencyFenceError — we do not
  "helpfully" convert; a wrong-currency payment is a bug, not a UX moment.
- All network I/O goes through an injected transport callable. Tests never
  touch the network, and a gateway with no credentials raises
  NotConfiguredError instead of pretending to charge cards.
- Every gateway response is normalized into a GatewayResult with
  provenance (which gateway, which api_version) so reconciliation can
  re-derive every number.
"""

from .gateway import (
    CurrencyFenceError,
    GatewayError,
    GatewayResult,
    GatewayTimeout,
    NotConfiguredError,
    PaystackGateway,
    PaymentGateway,
    StripeGateway,
    gateway_for_currency,
)
from .intent import (
    IntentState,
    LEGAL_INTENT_TRANSITIONS,
    PaymentIntent,
    IntentStore,
    capture_on_completion,
)
from .posting import (
    CommissionPosting,
    PaymentPosting,
    post_payment_to_invoice,
    commission_for_captured_payment,
)

__all__ = [
    "CurrencyFenceError", "GatewayError", "GatewayResult", "GatewayTimeout",
    "NotConfiguredError", "PaystackGateway", "PaymentGateway",
    "StripeGateway", "gateway_for_currency",
    "IntentState", "LEGAL_INTENT_TRANSITIONS", "PaymentIntent", "IntentStore",
    "capture_on_completion",
    "CommissionPosting", "PaymentPosting", "post_payment_to_invoice",
    "commission_for_captured_payment",
]
