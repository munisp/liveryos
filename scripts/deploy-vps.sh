#!/usr/bin/env bash
# deploy-vps.sh — idempotent LiveryOS bootstrap for Ubuntu 24.04 (see
# docs/deployment/vps-provisioning.md). Safe to re-run: every step checks
# before it acts. Run as the livery user from the repo root.
set -euo pipefail

ENV_FILE="infra/.env"
COMPOSE_FILES="-f infra/docker-compose.yml -f infra/docker-compose.prod.yml"

echo "== 1/5 docker =="
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
  echo "docker installed — LOG OUT AND BACK IN, then re-run this script"
  exit 0
fi
docker compose version >/dev/null

echo "== 2/5 env file =="
if [ ! -f "$ENV_FILE" ]; then
  echo "infra/.env missing — copy infra/.env.example and fill every CHANGEME" >&2
  exit 1
fi
if grep -q CHANGEME "$ENV_FILE"; then
  echo "infra/.env still contains CHANGEME placeholders:" >&2
  grep -n CHANGEME "$ENV_FILE" >&2
  exit 1
fi

echo "== 3/5 firewall =="
if command -v ufw >/dev/null 2>&1 && ! sudo ufw status | grep -q "Status: active"; then
  sudo ufw allow OpenSSH
  sudo ufw allow 80,443/tcp
  sudo ufw default deny incoming
  sudo ufw -y enable
fi

echo "== 4/5 images =="
docker compose $COMPOSE_FILES --env-file "$ENV_FILE" pull

echo "== 5/5 up =="
docker compose $COMPOSE_FILES --env-file "$ENV_FILE" up -d
docker compose $COMPOSE_FILES ps

cat <<'NEXT'

Bootstrap complete. Next (see docs/deployment/vps-provisioning.md):
  1. Wait for Nominatim import (docker logs -f liveryos-nominatim-1) — HOURS.
  2. Drop Valhalla tiles into the valhalla-tiles volume; restart valhalla.
  3. Provision the first tenant: scripts/new-tenant.sh <slug> <PT|NG> <email>
  4. Create the tenant's Keycloak realm.
  5. Schedule backups: crontab -e → 17 3 * * * /home/livery/liveryos/scripts/backup.sh
NEXT
