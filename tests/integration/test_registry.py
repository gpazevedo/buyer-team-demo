"""Live E2E tests for the registry config group + progressive disclosure.

Doubly opt-in: needs RUN_INTEGRATION=1 *and* RUN_INTEGRATION_INVOKE=1.
Validates that the seeded ``registry`` config group matches the deployed AgentCore
runtimes, and that the skill runtime's progressive-disclosure tools return
well-formed responses.
"""

import json
import os
import uuid

import pytest

from .conftest import EXPECTED_RUNTIMES, REGION

_INVOKE_ENABLED = os.getenv("RUN_INTEGRATION_INVOKE") == "1"

pytestmark = pytest.mark.skipif(
    not _INVOKE_ENABLED, reason="set RUN_INTEGRATION_INVOKE=1 to validate registry + runtimes"
)

ENV = os.getenv("ENV", "dev")

# ── helpers ─────────────────────────────────────────────────────────────


def _system_config_item(group: str, key: str = "default") -> dict:
    import boto3
    from botocore.config import Config

    ddb = boto3.resource(
        "dynamodb", region_name=REGION, config=Config(connect_timeout=10, read_timeout=30)
    )
    resp = ddb.Table(f"{ENV}-system-config").get_item(
        Key={"config_group": group, "config_key": key}
    )
    item = resp.get("Item")
    if not item:
        pytest.fail(f"system-config item {group}/{key} not found")
    return json.loads(item["config_json"])


def _runtime_names(control) -> set[str]:
    return {r["agentRuntimeName"] for r in control.list_agent_runtimes().get("agentRuntimes", [])}


def _a2a_message(text: str) -> bytes:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": f"reg-{uuid.uuid4().hex[:8]}",
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "messageId": uuid.uuid4().hex,
                    "parts": [{"kind": "text", "text": text}],
                }
            },
        }
    ).encode()


# ── registry config group ───────────────────────────────────────────────


def test_registry_config_group_exists_and_has_all_agents():
    """The dev-system-config 'registry' item exists and maps every logical agent."""
    registry = _system_config_item("registry")
    agents = registry["agents"]
    expected = {
        "kraljic_classifier",
        "spot_bidding",
        "leverage_auction",
        "bottleneck_negotiation",
        "strategic_partnership",
        "bid_evaluation",
        "award_comms",
    }
    assert set(agents) == expected, f"agent keys mismatch: {set(agents) ^ expected}"
    for name, info in agents.items():
        assert info["protocol"] == "A2A", f"{name}: expected A2A protocol"
        assert "runtime_name" in info, f"{name}: missing runtime_name"
        assert "capability" in info, f"{name}: missing capability"
        assert "model_tier" in info, f"{name}: missing model_tier"


def test_registry_mcp_servers_and_skills():
    """The registry includes all MCP servers and skills."""
    registry = _system_config_item("registry")
    mcp = registry["mcp_servers"]
    expected_mcp = {
        "skill_runtime",
        "dynamodb_master_data",
        "step_functions_orchestrator",
        "tenant_mdm_emulator",
    }
    assert set(mcp) == expected_mcp, f"mcp server keys mismatch: {set(mcp) ^ expected_mcp}"
    for name, info in mcp.items():
        assert info["protocol"] == "MCP", f"{name}: expected MCP protocol"

    skills = registry["skills"]
    expected_skills = {"integration", "test_tenant", "test_tenant_master"}
    assert set(skills) == expected_skills, f"skill keys mismatch: {set(skills) ^ expected_skills}"
    assert "ingest_purchase_requisitions" in skills["integration"]["capabilities"]


def test_registry_runtime_names_match_agentcore(agentcore_control):
    """Every agent runtime name in the registry resolves in AgentCore."""
    registry = _system_config_item("registry")
    deployed = _runtime_names(agentcore_control)
    for logical, info in registry["agents"].items():
        rt = info["runtime_name"]
        assert rt in deployed, (
            f"registry agent {logical!r} references runtime {rt!r} — not in AgentCore"
        )
        assert rt in EXPECTED_RUNTIMES, f"{rt!r} not in EXPECTED_RUNTIMES — update conftest.py?"


# ── progressive disclosure (L1/L2) — offline, import server module ──────
# The skill runtime is an MCP server (not A2A), so invoke_agent_runtime
# doesn't apply. Test the logic directly: catalog() manifest and
# skill_manual() SKILL.md reading.


def test_catalog_returns_all_manifest_capabilities():
    """L1 catalog() returns every capability from the manifest, no network."""
    import os as _os
    import sys

    _os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[4]))
    import asyncio

    from skill_runtime.server import catalog  # noqa: E402

    items = asyncio.run(catalog())
    names = {i["name"] for i in items}
    assert names == {"ingest_purchase_requisitions", "reset", "load_datasets", "validate_datasets"}
    for i in items:
        assert isinstance(i["name"], str) and len(i["name"]) > 0
        assert isinstance(i["summary"], str) and len(i["summary"]) > 0


def test_skill_manual_returns_skil_md_content():
    """L2 skill_manual returns the SKILL.md text for a known capability."""
    import os as _os
    import sys

    _os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[4]))
    import asyncio

    from skill_runtime.server import skill_manual  # noqa: E402

    result = asyncio.run(skill_manual("ingest_purchase_requisitions"))
    assert result["capability"] == "ingest_purchase_requisitions"
    assert result["skill"] == "integration"
    assert "Status-driven" in result["manual"]
    assert "ingest_purchase_requisitions" in result["manual"]


def test_skill_manual_unknown_capability_returns_error():
    """L2 skill_manual returns error dict for unknown capability."""
    import os as _os
    import sys

    _os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[4]))
    import asyncio

    from skill_runtime.server import skill_manual  # noqa: E402

    result = asyncio.run(skill_manual("nonexistent"))
    assert "error" in result
    assert "unknown capability" in result["error"]
