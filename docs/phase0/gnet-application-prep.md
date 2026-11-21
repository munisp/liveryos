# GNet Application Prep — The Moat Play

Context: the report's core strategic finding is that the moat in this industry is the affiliate network, not the software. GNet Connect (GRiDD Technologies) is the neutral exchange: one REST API (JSON/XML, token auth, webhooks), the GRES standard developed with the NLA, 65+ connected systems, 6,000+ operators in 90+ countries. GroundAlliance is notably absent from GNet's supported-systems list. Building for GNet from day one is how a new platform inherits a network instead of building one.

## What to prepare before applying

1. **Legal entity + product identity.** Company details, product name (LiveryOS), website, demo environment. GNet onboards *systems*, not just companies — you are applying as a software platform that will connect its tenants.
2. **Technical readiness evidence.** GRES conformance is the bar:
   - REST endpoints for booking create/update/cancel, status webhooks, quote exchange;
   - GRES field mapping (reservation, passenger, vehicle class, rate, status codes);
   - sandbox-able environment with test tenants.
   Target: our adapter passes GRES conformance against their sandbox before first tenant cutover.
3. **Operator references.** Even pre-launch: 2–3 letters of intent from operators (the first migration candidates) stating they will farm in/out via the platform. Network applicants with demand attached get prioritized.
4. **Geographic pitch.** Portugal + Nigeria is a differentiator: GNet's European coverage is deep, West African coverage is thin — a platform bringing Lagos operators onto the exchange is valuable to GNet, not just to us.

## Build implication (feeds Phase 2–3)

- The affiliate engine (report Ch. 3 §3.2.6) is designed GRES-shaped: farm-in/farm-out as first-class entities with per-partner split rules and blocking lists, so "GNet partner" is a configuration of the engine, not a special case.
- Event catalog already reserves the hooks: `booking.created`, `trip.assigned`, `trip.status_changed`, `invoice.paid` map onto GNet webhook payloads.
- Do NOT build a proprietary network protocol first. GRES-first; proprietary extensions only where GRES is silent (e.g., rich ETA streaming).

## Risks

- GRES conformance timeline is vendor-controlled; budget 4–8 weeks of conformance ping-pong in Phase 3.
- GNet terms for platform (vs operator) members differ — get the platform agreement before pricing the affiliate feature to tenants.
- If GNet onboarding stalls, the affiliate engine still works operator-to-operator via direct API keys — GNet is the accelerator, not a dependency.
