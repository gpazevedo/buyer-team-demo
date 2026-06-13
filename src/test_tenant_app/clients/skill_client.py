"""SkillClient — calls the Test Tenant Skill (PRD-012).

SKILL_MODE=stub  reads JSON fixtures from the fixtures/ directory.
SKILL_MODE=live  calls the real AgentCore Gateway (not implemented yet).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

SKILL_MODE = os.getenv("SKILL_MODE", "stub")
_FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load(name: str):
    return json.loads((_FIXTURES / name).read_text())


def _query_by_tenant(table_name: str, tenant_id: str) -> list[dict]:
    """Live read: all rows for a tenant from a (tenant_id, *) domain table."""
    from boto3.dynamodb.conditions import Key

    from test_tenant_app.clients.ddb import table, to_native

    items, start_key = [], None
    while True:
        kwargs = {"KeyConditionExpression": Key("tenant_id").eq(tenant_id)}
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        resp = table(table_name).query(**kwargs)
        items.extend(resp.get("Items", []))
        start_key = resp.get("LastEvaluatedKey")
        if not start_key:
            return to_native(items)


class SkillClient:
    def get_dataset_status(self, tenant_id: str) -> dict:
        if SKILL_MODE == "stub":
            return _load("dataset_status.json")
        from boto3.dynamodb.conditions import Key

        from test_tenant_app.clients.ddb import table

        cats = table("categories").query(
            KeyConditionExpression=Key("tenant_id").eq(tenant_id), Select="COUNT"
        ).get("Count", 0)
        sups = table("suppliers").query(
            KeyConditionExpression=Key("tenant_id").eq(tenant_id), Select="COUNT"
        ).get("Count", 0)
        return {
            "tenant_id": tenant_id,
            "categories": cats,
            "suppliers": sups,
            "negotiations": 0,
            "phases": [],
            "all_phases_complete": cats > 0,
        }

    def load_datasets(self, tenant_id: str, datasets: list[str]) -> dict:
        if SKILL_MODE == "stub":
            return {"status": "ok", "datasets": datasets}
        raise NotImplementedError("load runs via the skill runtime (not wired)")

    def validate_datasets(self, tenant_id: str) -> dict:
        if SKILL_MODE == "stub":
            return {"valid": True, "issues": []}
        raise NotImplementedError("validate runs via the skill runtime (not wired)")

    def reset_tenant_data(self, tenant_id: str) -> dict:
        if SKILL_MODE == "stub":
            return {"status": "reset"}
        raise NotImplementedError("reset runs via the skill runtime (not wired)")

    def get_categories(self, tenant_id: str) -> list[dict]:
        if SKILL_MODE == "stub":
            return _load("categories.json")
        # Normalize persisted rows to the app contract: domain stores
        # `kraljic_quadrant` (UPPER) and omits annual_spend.
        rows = _query_by_tenant("categories", tenant_id)
        for r in rows:
            r.setdefault("quadrant", str(r.get("kraljic_quadrant", "non_critical")).lower())
            r.setdefault("annual_spend", 0)
        return rows

    def get_suppliers(self, tenant_id: str) -> list[dict]:
        if SKILL_MODE == "stub":
            return _load("suppliers.json")
        return _query_by_tenant("suppliers", tenant_id)

    def get_items(self, tenant_id: str) -> list[dict]:
        if SKILL_MODE == "stub":
            return _load("items.json")
        return _query_by_tenant("items", tenant_id)

    def get_negotiations(self, tenant_id: str) -> list[dict]:
        return []


skill_client = SkillClient()
