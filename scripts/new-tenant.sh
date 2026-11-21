#!/usr/bin/env bash
# new-tenant.sh — provision one tenant site on the private bench.
# Tenancy decision (report Ch. 5 §5.1.2): SITE-PER-TENANT.
# Rationale: a SaaS selling data isolation needs per-tenant DBs, per-tenant
# backups, and noisy-neighbor containment; shared-site-with-tenant-field was
# rejected. Cost is higher per tenant — priced into the €89/€179 + NGN tiers.
set -euo pipefail

TENANT_SLUG="${1:?usage: new-tenant.sh <slug> <market:PT|NG> <admin-email>}"
MARKET="${2:?PT or NG}"
ADMIN_EMAIL="${3:?admin email}"
BENCH_DIR="${BENCH_DIR:-/home/frappe/frappe-bench}"

case "$MARKET" in
  PT) CURRENCY="EUR"; COUNTRY="Portugal"; TZ="Europe/Lisbon" ;;
  NG) CURRENCY="NGN";  COUNTRY="Nigeria";  TZ="Africa/Lagos" ;;
  *) echo "market must be PT or NG" >&2; exit 2 ;;
esac

SITE="${TENANT_SLUG}.liveryos.local"

cd "$BENCH_DIR"

# 1. Site + apps: ERPNext first, then our custom core on the same bench.
bench new-site "$SITE" \
  --db-root-username root --db-root-password "${DB_ROOT_PASSWORD:?}" \
  --admin-password "$(openssl rand -base64 24)" \
  --install-app erpnext
bench --site "$SITE" install-app livery_core
bench --site "$SITE" install-app hrms   # Frappe HR — chauffeur-compliance backlog (gap item 12)

# 2. Market defaults: currency, timezone, country, holiday list.
bench --site "$SITE" set-config default_currency "$CURRENCY"
bench --site "$SITE" set-config time_zone "$TZ"
bench --site "$SITE" execute frappe.setup.utils.set_defaults --kwargs "{\"country\": \"$COUNTRY\"}" || true

# 3. Tenant admin.
bench --site "$SITE" add-user "$ADMIN_EMAIL" --first-name "$TENANT_SLUG" --last-name Admin || true

# 4. Keycloak realm per tenant (report Ch. 5 §5.3.2) — placeholder until
#    automation lands in Phase 3; for now, emit the command for the runbook.
echo "TODO: create Keycloak realm '${TENANT_SLUG}' at \${LIVERY_KEYCLOAK_URL}"

# 5. Register tenant in the platform registry (billing/metering upstream).
echo "{\"slug\": \"${TENANT_SLUG}\", \"market\": \"${MARKET}\", \"site\": \"${SITE}\", \"currency\": \"${CURRENCY}\", \"created\": \"$(date -u +%FT%TZ)\"}" \
  >> "${REGISTRY_FILE:-/tmp/liveryos-tenants.jsonl}"

echo "tenant ${TENANT_SLUG} (${MARKET}) provisioned at ${SITE}"
