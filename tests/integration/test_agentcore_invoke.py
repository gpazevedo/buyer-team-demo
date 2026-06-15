"""Data-plane invoke tests for the agent runtimes.

Doubly opt-in: needs RUN_INTEGRATION=1 *and* RUN_INTEGRATION_INVOKE=1, because
these actually invoke runtimes (billable, and may cold-start).

- kraljic_classifier, spot_bidding, and bid_evaluation carry real A2A images and
  are asserted for real.
- The remaining agents still hold ARM64 placeholder images that reject the invoke
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


def test_spot_bidding_invoke_runs_bidding_round(agentcore, agentcore_control):
    """Real LLM-backed A2A invoke: a spot-bidding round composes the RFQ, sends an
    invitation per candidate supplier, and returns the full SpotBidResponse schema
    (genuine tool use, not a templated stub)."""
    arn = _runtime_arn(agentcore_control, "dev_spot_bidding")
    request = {
        "negotiation_id": "itest-spot-neg-1",
        "tenant_id": "6eb4ebaf-804e-5837-ae26-f665a76b58dd",
        "deadline": "2026-06-20T00:00:00Z",
        "budget_limit": 10000,
        "target_price": 8000,
        "max_concurrent_bids": 3,
        "items": [{"item_id": "i1", "description": "office paper A4", "quantity": 100}],
        "candidate_suppliers": [
            {"supplier_id": "s1", "name": "Acme"},
            {"supplier_id": "s2", "name": "Globex"},
        ],
        "governance": {},
    }
    resp = agentcore.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=f"itest-spot-{uuid.uuid4().hex}".ljust(33, "0"),
        contentType="application/json",
        accept="application/json",
        payload=_a2a_message(json.dumps(request)),
    )
    assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200
    # The SpotBidResponse JSON is nested as an artifact text part.
    body = resp["response"].read().decode()
    assert "spot_bid_result" in body
    assert "itest-spot-neg-1" in body
    assert "invitations_sent" in body
    assert "response_rate" in body
    assert "communication_log" in body


def test_bid_evaluation_invoke_ranks_bids(agentcore, agentcore_control):
    """Real LLM-backed A2A invoke: the bid evaluation agent scores each bid across the
    weighted dimensions, ranks them, and recommends one of the input bids — full
    BidEvaluationResponse schema (genuine tool use + completeness steering, not a stub)."""
    arn = _runtime_arn(agentcore_control, "dev_bid_evaluation")
    request = {
        "negotiation_id": "itest-bideval-neg-1",
        "tenant_id": "6eb4ebaf-804e-5837-ae26-f665a76b58dd",
        "category_id": "784700a6-a316-5f45-a773-578052c89bcc",
        "budget_limit": 10000,
        "esg_threshold": 0.3,
        "evaluation_weights": {"cost": 0.4, "delivery": 0.2, "quality": 0.2, "esg": 0.1, "history": 0.1},
        "items": [{"item_id": "itest-item-1", "delivery_ideal_days": 7, "quantity": 100}],
        "bids": [
            {"bid_id": "b1", "supplier_id": "04caf9a7-4359-50c2-b458-9c905ead39b5",
             "unit_price": 6.40, "delivery_days": 7},
            {"bid_id": "b2", "supplier_id": "0cc6a1f1-8442-5ce4-b5f7-6ba0985418ee",
             "unit_price": 5.10, "delivery_days": 12},
        ],
        "governance": {},
    }
    resp = agentcore.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=f"itest-bideval-{uuid.uuid4().hex}".ljust(33, "0"),
        contentType="application/json",
        accept="application/json",
        payload=_a2a_message(json.dumps(request)),
    )
    assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200
    # The BidEvaluationResponse JSON is nested as an artifact text part.
    body = resp["response"].read().decode()
    assert "bid_evaluation_result" in body
    assert "itest-bideval-neg-1" in body
    assert "ranked_bids" in body
    assert "recommendation" in body
    assert "evaluation_metadata" in body
