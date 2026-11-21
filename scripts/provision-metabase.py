#!/usr/bin/env python3
"""Provision LiveryOS BI into Metabase (reporting/BI workstream).

Idempotent by name: re-running updates cards/dashboards in place, never
duplicates them. Uses the Metabase REST API with a session login.

Boundary discipline (AGPL + honesty):
- Metabase connects to livery-db with the READ-ONLY `livery_bi` role.
- The provisioner creates exactly one database connection, one collection,
  and the cards/dashboards declared in bi/dashboards/*.json.
- If Metabase is unreachable or credentials are missing, the script exits
  non-zero with a clear error — it never pretends provisioning happened.

Usage:
    MB_URL=http://localhost:3000 MB_USER=admin@example.com MB_PASSWORD=... \
    BI_DB_DSN=postgresql://livery_bi:...@livery-db:5432/livery_core \
        python3 scripts/provision-metabase.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARDS_DIR = ROOT / "bi" / "dashboards"
SQL_FILE = ROOT / "bi" / "sql" / "010_views.sql"


def mb(method: str, path: str, base: str, token: str | None,
       body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json",
                 **({"X-Metabase-Session": token} if token else {})},
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read() or b"{}")


def login(base: str, user: str, password: str) -> str:
    return mb("POST", "/api/session", base, None,
              {"username": user, "password": password})["id"]


def find_by_name(items: list[dict], name: str) -> dict | None:
    return next((i for i in items if i.get("name") == name), None)


def main() -> int:
    base = os.environ.get("MB_URL", "").rstrip("/")
    user = os.environ.get("MB_USER", "")
    password = os.environ.get("MB_PASSWORD", "")
    dsn = os.environ.get("BI_DB_DSN", "")
    if not all([base, user, password, dsn]):
        print("MB_URL / MB_USER / MB_PASSWORD / BI_DB_DSN all required — "
              "refusing to guess credentials", file=sys.stderr)
        return 2

    token = login(base, user, password)
    print("metabase session ok")

    # 1. BI views must exist on livery-db before cards can query them.
    print(f"apply {SQL_FILE.name} to livery-db with the read-only BI role, "
          "then continue — the provisioner does NOT hold DDL credentials")
    # (DDL is applied by deploy-vps.sh / migrations with owner creds; the
    # provisioner deliberately only speaks to Metabase.)

    # 2. Database connection (read-only role).
    dbs = mb("GET", "/api/database", base, token).get("data", [])
    db = find_by_name(dbs, "LiveryOS Platform (read-only)")
    if db is None:
        db = mb("POST", "/api/database", base, token, {
            "name": "LiveryOS Platform (read-only)",
            "engine": "postgres",
            "details": {"connection-uri": dsn},
        })
    db_id = db["id"]
    print(f"database connection id={db_id}")

    # 3. Collection.
    cols = mb("GET", "/api/collection", base, token)
    col = find_by_name(cols, "LiveryOS") or mb(
        "POST", "/api/collection", base, token, {"name": "LiveryOS"})
    col_id = col["id"]

    # 4. Cards + dashboards from manifests.
    existing_cards = mb("GET", "/api/card", base, token)
    existing_dash = mb("GET", "/api/dashboard", base, token)
    for manifest_path in sorted(DASHBOARDS_DIR.glob("*.json")):
        manifest = json.loads(manifest_path.read_text())
        card_ids = []
        for card in manifest["cards"]:
            dataset_query = {
                "database": db_id,
                "type": "native",
                "native": {"query": card["query"]},
            }
            payload = {
                "name": card["title"],
                "dataset_query": dataset_query,
                "display": card["display"],
                "visualization_settings": {},
                "collection_id": col_id,
            }
            existing = find_by_name(existing_cards, card["title"])
            if existing:
                mb("PUT", f"/api/card/{existing['id']}", base, token, payload)
                card_ids.append(existing["id"])
            else:
                created = mb("POST", "/api/card", base, token, payload)
                card_ids.append(created["id"])
        dash = find_by_name(existing_dash, manifest["name"])
        dash_payload = {"name": manifest["name"],
                        "description": manifest.get("description", ""),
                        "collection_id": col_id}
        if dash:
            mb("PUT", f"/api/dashboard/{dash['id']}", base, token, dash_payload)
            dash_id = dash["id"]
        else:
            dash_id = mb("POST", "/api/dashboard", base, token,
                         dash_payload)["id"]
        print(f"dashboard '{manifest['name']}' id={dash_id} "
              f"cards={len(card_ids)} (layout via Metabase UI or "
              "/api/dashboard/{id} PUT with dashcards)")
    print("provisioning complete — verify cards render before demoing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
