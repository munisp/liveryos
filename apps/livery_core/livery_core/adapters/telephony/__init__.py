"""Telephony & messaging (GA parity gap (a)).

GA sells phone-number masking ($30/mo) and a Twilio-powered conversation
module ($20/mo). LiveryOS builds the same capability on open source:

- LiveKit (Apache-2.0) is the WebRTC/SIP substrate: voice calls between
  passenger and driver run through masked LiveKit rooms; SIP egress lets a
  "call driver" button ring a real phone without exposing the number.
- Pipecat (BSD-2, pipecat-ai) is the voice-agent framework: after-hours
  booking line, "where is my driver" bot. The bot NEVER pretends to be
  human and never fabricates a booking — it emits intents for the booking
  pipeline.
- Notifications (SMS / WhatsApp / voice drop) are composed here and
  delivered through the event outbox, so delivery is at-least-once and
  dead-lettered, never silently lost.

Honesty rules:
- A masked number that isn't provisioned raises NotProvisionedError; the
  UI shows "calling unavailable" rather than dialing anything fake.
- The LiveKit token builder produces real HS256 JWTs, but joining a room
  requires a running LiveKit server; absence of credentials raises
  NotConfiguredError (same discipline as payments).
- Notifications are QUEUED, never claimed sent — `queue_notifications`
  returns rows for the outbox; only the dispatcher marks them delivered.
"""

from .masking import (
    MaskingPool,
    MaskingAssignment,
    NotProvisionedError,
    NumberInUseError,
)
from .livekit_token import (
    LiveKitConfig,
    VideoGrant,
    build_access_token,
    room_for_booking,
)
from .notify import (
    Channel,
    NotificationRequest,
    compose_for_event,
    default_channel_policy,
)
from .pipecat_bot import (
    PipecatPipelineSpec,
    build_pipeline_spec,
    voice_agent_config,
)

__all__ = [
    "MaskingPool", "MaskingAssignment", "NotProvisionedError", "NumberInUseError",
    "LiveKitConfig", "VideoGrant", "build_access_token", "room_for_booking",
    "Channel", "NotificationRequest", "compose_for_event",
    "default_channel_policy",
    "PipecatPipelineSpec", "build_pipeline_spec", "voice_agent_config",
]
