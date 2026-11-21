"""Booking widget + corporate booking tool domain (GA parity gap (d)).

GA ships an embeddable booking widget (iframe/script on the client's own
site) and a corporate booking tool (PO numbers, hierarchies, monthly
billing). LiveryOS domain layer:

- widget_keys.py: embed-key issuance/validation, origin allowlist, token-
  bucket rate limiting. A widget key is NOT a login — it can only create
  quote/booking requests for its tenant, and only from allowed origins.
- corporate.py: corporate accounts with PO policy, cost centers,
  traveler profiles, and approval rules. A booking that fails policy is
  REJECTED with a readable reason — never silently booked anyway.
"""

from .widget_keys import (
    EmbedKey,
    KeyStore,
    RateLimitExceeded,
    WidgetOriginError,
    issue_key,
    validate_request,
)
from .corporate import (
    ApprovalDecision,
    CorporateAccount,
    CorporatePolicyError,
    CostCenter,
    TravelerProfile,
    evaluate_booking,
)

__all__ = [
    "EmbedKey", "KeyStore", "RateLimitExceeded", "WidgetOriginError",
    "issue_key", "validate_request",
    "ApprovalDecision", "CorporateAccount", "CorporatePolicyError",
    "CostCenter", "TravelerProfile", "evaluate_booking",
]
