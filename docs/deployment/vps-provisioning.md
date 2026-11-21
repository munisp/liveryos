# VPS Provisioning Guide — LiveryOS Production

One VPS per environment to start (staging + production). Hetzner, Linode,
or DigitalOcean — anything with KVM, NVMe, and a real public IP. This guide
is written for Ubuntu 24.04 LTS.

## 1. Sizing (honest numbers, not vendor minimums)

| Tier | Spec | Fits | Why |
|------|------|------|-----|
| **Start** | 8 vCPU / 16 GB / 160 GB NVMe | 1–5 tenants, one market | Nominatim (one country extract) + Valhalla tiles + bench are the RAM floor; 8 GB swaps under import load |
| **Growth** | 16 vCPU / 32 GB / 320 GB NVMe | 5–20 tenants, both markets | TWO Nominatim imports (Portugal + Nigeria) — Nigeria OSM extract is larger and import takes hours; do not run both on the Start tier |
| **NG budget** | 4 vCPU / 8 GB | demo/pilot only, Lagos | GraphHopper instead of Valhalla (report Ch. 4 §4.2.1), no Nominatim — use the PT instance over VPN or a shared geocoder |

Rule: Nominatim import needs ~2× the final DB size in free disk during
import. A 160 GB disk is tight for two countries — budget tier before
importing the second market, not after it fails at 93%.

## 2. Base OS (10 minutes)

```bash
# as root on a fresh Ubuntu 24.04
apt update && apt -y upgrade
apt -y install ufw fail2ban unattended-upgrades curl git
adduser --disabled-password --gecos "" livery && usermod -aG sudo livery
# SSH key-only auth (paste your pubkey first), then:
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl reload ssh
ufw allow OpenSSH && ufw allow 80,443/tcp && ufw default deny incoming && ufw -y enable
```

Traccar device ports (5000–5150) stay CLOSED until real GPS devices exist —
open them per-protocol then, and prefer the driver PWA's HTTPS position
reporting (no device ports at all).

## 3. Deploy

```bash
sudo -iu livery
git clone https://github.com/munisp/liveryos.git && cd liveryos
cp infra/.env.example infra/.env && ${EDITOR} infra/.env   # fill every CHANGEME
bash scripts/deploy-vps.sh                                  # idempotent bootstrap
```

`deploy-vps.sh` installs Docker if missing, validates the env file, pulls
images, and runs `docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml up -d`.

## 4. DNS + TLS

Caddy terminates TLS (automatic Let's Encrypt). Point these records at the VPS:

```
bench.example.com      A  <vps-ip>     # per-tenant sites: <slug>.bench.example.com (wildcard)
auth.example.com       A  <vps-ip>     # Keycloak
rt.example.com         A  <vps-ip>     # Centrifugo websocket
bi.example.com         A  <vps-ip>     # Metabase (operator-only)
```

Wildcard `*.bench.example.com` is what makes site-per-tenant work without
per-tenant DNS edits. Edit `infra/Caddyfile` for your domain before first boot.

## 5. First boot order (matters)

1. `docker compose ps` — all healthy (livery-db and mariadb healthchecks first)
2. Nominatim import runs on first start: **hours** for Portugal, longer for
   Nigeria. Watch `docker logs -f liveryos-nominatim-1` — do not provision
   tenants until it reports ready.
3. Valhalla tiles: drop the Geofabrik extract into the `valhalla-tiles`
   volume, restart the valhalla service.
4. Lagos operators: run the ground-truth sprint BEFORE selling ETAs —
   compare Valhalla Lagos ETAs against 20 real trips (report Ch. 4 §4.2.1).
   If divergence >25%, keep `eta_source="estimate"` conservative and tune
   `DEFAULT_CITY_SPEED_KMH` per market.

## 6. First tenant

```bash
docker exec -it liveryos-bench-1 bash /workspace/apps/livery_core/scripts/new-tenant.sh acme PT ops@acme.example
docker exec liveryos-livery-db-1 psql -U postgres -d livery_core -c 'select * from tenant_registry;'
```

Then create the Keycloak realm for the tenant (the script prints the TODO),
and point the tenant admin at `https://acme.bench.example.com`.

## 7. Backups (non-negotiable from day one)

`scripts/backup.sh` (nightly cron as livery user):
- `pg_dump` each livery-db database (platform truth: outbox, positions,
  registry, GA staging, affiliates)
- `mariadb-dump` the bench database (ERPNext DocType schema + data)
- tar of bench-sites volume (private files)
- optional `rclone`/S3 offsite copy — offsite is what makes it a backup
  rather than a hope
- restore drill monthly: a backup that has never been restored is a rumor

## 8. Monitoring floor

- `docker compose ps` + disk space alert at 75% (Nominatim growth)
- Centrifugo + bench uptime checks (Uptime Kuma or hosted equivalent)
- Dead-letter watch: `event_outbox` rows with `dead_lettered_at` — zero is
  the only acceptable steady state; anything else is a morning standup item
- Reconciliation verdict (parallel-run tenants): `cutover_gate()` output
  reviewed daily until cutover completes

## 9. What this environment could not verify (honest list)

The compose topology, scripts, and Caddyfile are syntax-validated and
CI-checked, but no live boot has happened here (no Docker in the build
environment). First real boot will surface image-tag drift and import
resource reality — budget one focused day for it, not one hour.
