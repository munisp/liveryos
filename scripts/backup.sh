#!/usr/bin/env bash
# backup.sh — nightly LiveryOS backup (docs/deployment/vps-provisioning.md §7).
# Platform truth lives in livery-db (Postgres); the bench DB (MariaDB) holds
# the ERPNext schema+data; bench-sites holds private files. All three, every
# night, plus an offsite copy when RCLONE_REMOTE is set.
set -euo pipefail

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="${BACKUP_DIR:-/home/livery/backups}/${STAMP}"
KEEP_DAYS="${KEEP_DAYS:-14}"
mkdir -p "$DEST"

COMPOSE_FILES="-f infra/docker-compose.yml -f infra/docker-compose.prod.yml"
cd "$(dirname "$0")/.."
source infra/.env

echo "== livery-db (Postgres) =="
for db in livery_core keycloak traccar; do
  docker compose $COMPOSE_FILES exec -T livery-db \
    pg_dump -U postgres -Fc "$db" > "$DEST/liverydb-${db}.dump"
done

echo "== bench (MariaDB) =="
docker compose $COMPOSE_FILES exec -T mariadb \
  mariadb-dump -u root -p"${DB_ROOT_PASSWORD}" --all-databases \
  | gzip > "$DEST/bench-mariadb.sql.gz"

echo "== bench-sites volume (private files) =="
docker run --rm -v liveryos_bench-sites:/sites -v "$DEST":/out alpine \
  tar czf "/out/bench-sites.tar.gz" -C /sites .

echo "== manifest =="
{
  echo "stamp=$STAMP"
  echo "git=$(git rev-parse HEAD 2>/dev/null || echo unknown)"
  sha256sum "$DEST"/* | sed "s|$DEST/||"
} > "$DEST/MANIFEST.txt"

echo "== offsite =="
if [ -n "${RCLONE_REMOTE:-}" ]; then
  rclone copy "$DEST" "${RCLONE_REMOTE}/liveryos/${STAMP}"
else
  echo "RCLONE_REMOTE not set — local copy only (offsite is what makes it a backup)"
fi

echo "== retention =="
find "$(dirname "$DEST")" -mindepth 1 -maxdepth 1 -type d -mtime "+${KEEP_DAYS}" -exec rm -rf {} +

echo "backup complete: $DEST"
