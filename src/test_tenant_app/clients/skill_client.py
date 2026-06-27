"""SkillClient — calls the Test Tenant Skill (PRD-012).

SKILL_MODE=stub  reads JSON fixtures from the fixtures/ directory.
SKILL_MODE=live  invokes the real AgentCore MCP runtime (dev_skill_runtime).
"""

from __future__ import annotations

import json
import os
import uuid
from functools import cache
from pathlib import Path

import boto3
from opentelemetry import propagate, trace

_tracer = trace.get_tracer("buyer-team.app.skill-client")

SKILL_MODE = os.getenv("SKILL_MODE", "stub")
_FIXTURES = Path(__file__).parent.parent / "fixtures"
_REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
_RUNTIME_NAME = f"{os.getenv('ENV', 'dev')}_skill_runtime"
_MCP_PROTOCOL_VERSION = "2024-11-05"


def _load(name: str):
    return json.loads((_FIXTURES / name).read_text())


@cache
def _control_client():
    return boto3.client("bedrock-agentcore-control", region_name=_REGION)


@cache
def _runtime_client():
    return boto3.client("bedrock-agentcore", region_name=_REGION)


@cache
def _skill_runtime_arn() -> str:
    for r in _control_client().list_agent_runtimes().get("agentRuntimes", []):
        if r["agentRuntimeName"] == _RUNTIME_NAME:
            return r["agentRuntimeArn"]
    raise RuntimeError(f"Skill runtime {_RUNTIME_NAME!r} not found")


def _invoke_skill_tool(tool_name: str, arguments: dict) -> dict:
    """Invoke a tool on the AgentCore MCP skill runtime and return the result dict.

    Wrapped in a client span whose W3C trace context is injected into the JSON-RPC
    `params._meta`; the skill runtime extracts it (AgentCore does not forward HTTP
    headers, but the request body is), so the app→skill hop is one distributed trace.
    """
    call_id = uuid.uuid4().hex
    with _tracer.start_as_current_span(f"skill-client.{tool_name}"):
        params: dict = {"name": tool_name, "arguments": arguments}
        carrier: dict = {}
        propagate.inject(carrier)
        if carrier:
            params["_meta"] = carrier
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": call_id,
                "method": "tools/call",
                "params": params,
            }
        ).encode()

        resp = _runtime_client().invoke_agent_runtime(
            agentRuntimeArn=_skill_runtime_arn(),
            payload=payload,
            qualifier="DEFAULT",
            contentType="application/json",
            accept="application/json, text/event-stream",
            mcpProtocolVersion=_MCP_PROTOCOL_VERSION,
        )

        # Response is SSE: "event: message\ndata: {...}\n\n"
        raw = resp["response"].read().decode()
        for line in raw.splitlines():
            if line.startswith("data: "):
                envelope = json.loads(line[6:])
                if "error" in envelope:
                    raise RuntimeError(f"Skill tool {tool_name!r} failed: {envelope['error']}")
                # MCP result content is [{type: text, text: <json>}]
                content = envelope.get("result", {}).get("content", [])
                for part in content:
                    if part.get("type") == "text":
                        return json.loads(part["text"])
        raise RuntimeError(f"No result from skill tool {tool_name!r}")


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

        cats = (
            table("categories")
            .query(KeyConditionExpression=Key("tenant_id").eq(tenant_id), Select="COUNT")
            .get("Count", 0)
        )
        sups = (
            table("suppliers")
            .query(KeyConditionExpression=Key("tenant_id").eq(tenant_id), Select="COUNT")
            .get("Count", 0)
        )
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
        return _invoke_skill_tool("load_datasets", {"tenant_id": tenant_id, "datasets": datasets})

    def validate_datasets(self, tenant_id: str) -> dict:
        if SKILL_MODE == "stub":
            return {"valid": True, "issues": []}
        return _invoke_skill_tool("validate_datasets", {"tenant_id": tenant_id})

    def reset_tenant_data(self, tenant_id: str) -> dict:
        if SKILL_MODE == "stub":
            return {"status": "reset"}
        return _invoke_skill_tool("reset", {"tenant_id": tenant_id})

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
        # Domain stores `estimated_unit_price`; the app contract is `estimated_price`.
        rows = _query_by_tenant("items", tenant_id)
        for r in rows:
            r.setdefault("estimated_price", r.get("estimated_unit_price", 0))
        return rows

    def get_negotiations(self, tenant_id: str) -> list[dict]:
        return []


skill_client = SkillClient()
