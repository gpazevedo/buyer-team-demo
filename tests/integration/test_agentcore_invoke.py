"""Data-plane invoke tests for the agent runtimes.

Doubly opt-in: needs RUN_INTEGRATION=1 *and* RUN_INTEGRATION_INVOKE=1, because
these actually invoke runtimes (billable, and may cold-start).

- kraljic_classifier carries a real A2A image and is asserted for real.
- The other agents still hold ARM64 placeholder images that reject the invoke
  contract (HTTP 424), so they stay xfail until real images are deployed.
"""
import json
import os
import uuid

import pytest

_INVOKE_ENABLED = os.getenv("RUN_INTEGRATION_INVOKE") == "1"

pytestmark = pytest.mark.skipif(
    not _INVOKE_ENABLED, reason="set RUN_INTEGRATION_INVOKE=1 to invoke runtimes"
)

_QUADRANTS = {"NON_CRITICAL", "LEVERAGE", "BOTTLENECK", "STRATEGIC"}


def _runtime_arn(control, name: str) -> str:
    resp = control.list_agent_runtimes()
    for r in resp.get("agentRuntimes", []):
        if r["agentRuntimeName"] == name:
            return r["agentRuntimeArn"]
    pytest.fail(f"runtime {name} not found")


def _a2a_message(text: str) -> bytes:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "itest-1",
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


def test_kraljic_classifier_invoke_classifies(agentcore, agentcore_control):
    """Real LLM-backed A2A invoke: a high/high category classifies STRATEGIC
    and returns the full KraljicClassificationResponse schema (genuine
    LLM reasoning + confidence, not a templated stub)."""
    arn = _runtime_arn(agentcore_control, "dev_kraljic_classifier")
    request = {
        "category_name": "Industrial Bearings",
        "profit_impact": 0.82,
        "supply_risk": 0.88,
        "annual_spend": 1200000,
        "supplier_count": 2,
        "classification_thresholds": {
            "profit_impact_threshold": 0.5,
            "supply_risk_threshold": 0.5,
        },
    }
    resp = agentcore.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=f"itest-kraljic-{uuid.uuid4().hex}".ljust(33, "0"),
        contentType="application/json",
        accept="application/json",
        payload=_a2a_message(json.dumps(request)),
    )
    assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200
    # The classification JSON is nested as an artifact text part.
    body = resp["response"].read().decode()
    assert "STRATEGIC" in body
    assert any(q in body for q in _QUADRANTS)
    assert "confidence" in body
    assert "reasoning" in body
    assert "contributing_factors" in body


@pytest.mark.xfail(reason="spot_bidding still holds the placeholder image (HTTP 424)", strict=False)
def test_spot_bidding_invoke_placeholder(agentcore, agentcore_control):
    """Documents the remaining gap: agents other than kraljic are still placeholders."""
    arn = _runtime_arn(agentcore_control, "dev_spot_bidding")
    resp = agentcore.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=f"itest-spot-{uuid.uuid4().hex}".ljust(33, "0"),
        contentType="application/json",
        accept="application/json",
        payload=_a2a_message("{}"),
    )
    assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200
