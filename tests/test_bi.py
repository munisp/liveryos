"""BI layer validation — dashboards only reference real views, every query
is tenant-scoped, revenue is never blended across currencies."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "livery_core"))

SQL = (ROOT / "bi" / "sql" / "010_views.sql").read_text()
DASHBOARDS = sorted((ROOT / "bi" / "dashboards").glob("*.json"))


def _defined_views() -> set:
    return set(re.findall(r"CREATE OR REPLACE VIEW (\w+)", SQL))


def test_dashboards_exist_and_parse():
    assert len(DASHBOARDS) >= 2
    for path in DASHBOARDS:
        manifest = json.loads(path.read_text())
        assert manifest["name"].startswith("LiveryOS")
        assert manifest["cards"], f"{path.name} has no cards"


def test_every_card_references_a_real_view():
    views = _defined_views()
    assert views  # sanity
    for path in DASHBOARDS:
        for card in json.loads(path.read_text())["cards"]:
            assert card["view"] in views, (
                f"{path.name}:{card['title']} references {card['view']} "
                "which is not defined in 010_views.sql")
            assert card["view"] in card["query"]


def test_every_dashboard_query_is_tenant_scoped():
    for path in DASHBOARDS:
        for card in json.loads(path.read_text())["cards"]:
            assert "{{tenant}}" in card["query"], (
                f"{path.name}:{card['title']} is not tenant-scoped — "
                "cross-tenant leakage in BI is a breach")


def test_revenue_view_groups_by_currency_never_blends():
    m = re.search(r"CREATE OR REPLACE VIEW v_bi_revenue_per_day AS(.*?)ORDER BY",
                  SQL, re.S)
    assert m, "revenue view missing"
    body = m.group(1)
    assert "payload->>'currency'" in body
    assert "GROUP BY tenant, day, payload->>'currency'" in body


def test_no_float_money_in_views():
    # money may be divided for display (minor/100) in dashboard queries,
    # but the VIEWS themselves keep integer minor units.
    assert "paid_minor" in SQL
    assert "float" not in SQL.lower()
    assert "::numeric" not in SQL  # no silent numeric coercion in views


def test_provisioner_requires_env_no_guessing():
    src = (ROOT / "scripts" / "provision-metabase.py").read_text()
    assert "refusing to guess credentials" in src
    assert "read-only" in src.lower()
