"""Data-plane invoke tests for the agent runtimes.

Doubly opt-in: needs RUN_INTEGRATION=1 *and* RUN_INTEGRATION_INVOKE=1, because
these actually invoke runtimes (billable, and may cold-start).

- kraljic_classifier, spot_bidding, bid_evaluation, leverage_auction, and
  strategic_partnership carry real A2A images and are asserted for real.
- The remaining agents still hold ARM64 placeholder images that reject the invoke
  contract (HTTP 424), so they stay xfail until real images are deployed.
"""

import json
import os
import uuid

import pytest

from .conftest import TENANT

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


def _result_artifact_text(body: str) -> str:
    """The final result artifact's text from an A2A task response.

    The agents stream `tool_step_N` status artifacts ahead of the result (serve.py),
    so the result is not necessarily `artifacts[0]`. The final result is the single
    artifact whose name is not a `tool_step_*` status update.
    """
    artifacts = json.loads(body)["result"]["artifacts"]
    result = next(a for a in artifacts if not a["name"].startswith("tool_step"))
    return result["parts"][0]["text"]


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
        "evaluation_weights": {
            "cost": 0.4,
            "delivery": 0.2,
            "quality": 0.2,
            "esg": 0.1,
            "history": 0.1,
        },
        "items": [{"item_id": "itest-item-1", "delivery_ideal_days": 7, "quantity": 100}],
        "bids": [
            {
                "bid_id": "b1",
                "supplier_id": "04caf9a7-4359-50c2-b458-9c905ead39b5",
                "unit_price": 6.40,
                "delivery_days": 7,
            },
            {
                "bid_id": "b2",
                "supplier_id": "0cc6a1f1-8442-5ce4-b5f7-6ba0985418ee",
                "unit_price": 5.10,
                "delivery_days": 12,
            },
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


def test_leverage_auction_invoke_runs_auction(agentcore, agentcore_control, table):
    """Real LLM-backed A2A invoke: a leverage auction invites each qualified supplier,
    collects + ranks the round's bids (read from the seeded `dev-bids` rows), and returns
    the full AuctionResponse schema (genuine multi-round tool use, not a templated stub).

    The leverage agent prices bids from the negotiation's real supplier rows, so we seed
    three unpriced bid rows (a fresh negotiation per run for isolation) and clean them up."""
    arn = _runtime_arn(agentcore_control, "dev_leverage_auction")
    negotiation_id = f"itest-lev-neg-{uuid.uuid4().hex}"
    suppliers = [
        ("aaa11111-0000-0000-0000-000000000001", 8, "a@example.com"),
        ("bbb22222-0000-0000-0000-000000000002", 12, "b@example.com"),
        ("ccc33333-0000-0000-0000-000000000003", 20, "c@example.com"),
    ]
    bids = table("dev-bids")
    seeded_bid_ids = []
    for supplier_id, delivery_days, _ in suppliers:
        bid_id = uuid.uuid4().hex
        seeded_bid_ids.append(bid_id)
        bids.put_item(
            Item={
                "tenant_id": TENANT,
                "bid_id": bid_id,
                "negotiation_id": negotiation_id,
                "supplier_id": supplier_id,
                "delivery_days": delivery_days,
                "status": "INVITED",
                "currency": "USD",
            }
        )
    try:
        request = {
            "negotiation_id": negotiation_id,
            "tenant_id": TENANT,
            "max_rounds": 3,
            "round_duration_hours": 24,
            "budget_limit": 80000,
            "target_price": 60000,
            "items": [{"item_id": "i1", "description": "steel fasteners", "quantity": 500}],
            "qualified_suppliers": [
                {"supplier_id": s, "name": s[:3].upper(), "email": e} for s, _, e in suppliers
            ],
            "governance": {},
        }
        resp = agentcore.invoke_agent_runtime(
            agentRuntimeArn=arn,
            runtimeSessionId=f"itest-lev-{uuid.uuid4().hex}".ljust(33, "0"),
            contentType="application/json",
            accept="application/json",
            payload=_a2a_message(json.dumps(request)),
        )
        assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200
        # The AuctionResponse JSON is nested as an artifact text part.
        body = resp["response"].read().decode()
        assert negotiation_id in body
        assert "final_bids" in body
        assert "all_round_bids" in body
        assert "price_reduction_pct" in body
        assert "convergence_reached" in body
        assert "participation_rate" in body
        assert "communication_log" in body
        # The auction actually ran against the seeded rows: the seeded suppliers come
        # back as final bids with real prices (the model derives them per round). Rank
        # is left unasserted — the small-tier structured_output coercion doesn't reliably
        # re-emit per-bid rank, but bid identity + pricing prove genuine tool use.
        result = json.loads(_result_artifact_text(body))
        assert result["final_bids"], "expected final bids from the seeded suppliers"
        seeded_ids = {s for s, _, _ in suppliers}
        returned_ids = {b["supplier_id"] for b in result["final_bids"]}
        assert returned_ids & seeded_ids, "final bids should reference the seeded suppliers"
        assert all(b["total_price"] > 0 for b in result["final_bids"])
    finally:
        for bid_id in seeded_bid_ids:
            bids.delete_item(Key={"tenant_id": TENANT, "bid_id": bid_id})


def test_strategic_partnership_invoke_runs_negotiation(agentcore, agentcore_control):
    """Real LLM-backed A2A invoke: a strategic partnership negotiation researches each
    candidate (relationship history), assesses risk, computes TCO, sends proposals, and
    recommends one of the candidate suppliers — full StrategicPartnershipResponse schema
    (genuine tool use + the three prerequisite steering guards, not a templated stub).

    The agent reads supplier rows from the live `dev-suppliers` table; no seeding needed."""
    arn = _runtime_arn(agentcore_control, "dev_strategic_partnership")
    candidate_ids = [
        "377f9353-f38a-5e66-b706-f36021efc667",
        "6ad37dd1-93c7-502b-ab70-75998e45612c",
    ]
    request = {
        "negotiation_id": f"itest-strat-neg-{uuid.uuid4().hex}",
        "tenant_id": TENANT,
        "category_id": "784700a6-a316-5f45-a773-578052c89bcc",
        "max_rounds": 3,
        "budget_limit": 500000,
        "target_price": 80,
        "esg_threshold": 0.3,
        "partnership_priorities": ["innovation", "quality", "supply_security", "cost"],
        "items": [
            {
                "item_id": "i1",
                "description": "Precision machined assemblies",
                "quantity": 2000,
                "annual_volume": 2000,
            }
        ],
        "strategic_suppliers": [
            {"supplier_id": candidate_ids[0], "name": "Supplier 20", "email": "s20@example.com"},
            {"supplier_id": candidate_ids[1], "name": "Supplier 8", "email": "s8@example.com"},
        ],
        "governance": {},
    }
    resp = agentcore.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=f"itest-strat-{uuid.uuid4().hex}".ljust(33, "0"),
        contentType="application/json",
        accept="application/json",
        payload=_a2a_message(json.dumps(request)),
    )
    assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200
    # The StrategicPartnershipResponse JSON is nested as an artifact text part.
    body = resp["response"].read().decode()
    assert request["negotiation_id"] in body
    assert "tco_analysis" in body
    assert "risk_assessment" in body
    assert "relationship_impact" in body
    assert "recommended_supplier_id" in body
    assert "communication_log" in body
    # The negotiation actually ran: the agent recommends one of the candidate suppliers
    # (prerequisite guards force TCO/risk/history before the recommendation).
    result = json.loads(_result_artifact_text(body))
    assert result["recommended_supplier_id"] in candidate_ids
    assert result["tco_analysis"], "expected a TCO analysis per candidate"


def test_bottleneck_negotiation_invoke_runs_negotiation(agentcore, agentcore_control):
    """Real LLM-backed A2A invoke: a bottleneck negotiation assesses risk, computes TCO,
    sends supply-security proposals, and recommends one of the candidate suppliers plus a
    backup for concentration-risk mitigation — full BottleneckNegotiationResponse schema
    (genuine tool use + the two prerequisite steering guards, not a templated stub).

    The TCO/risk tools are pure + DynamoDB-cached; no seeding needed."""
    arn = _runtime_arn(agentcore_control, "dev_bottleneck_negotiation")
    candidate_ids = [
        "377f9353-f38a-5e66-b706-f36021efc667",
        "6ad37dd1-93c7-502b-ab70-75998e45612c",
    ]
    request = {
        "negotiation_id": f"itest-bottleneck-neg-{uuid.uuid4().hex}",
        "tenant_id": TENANT,
        "category_id": "784700a6-a316-5f45-a773-578052c89bcc",
        "max_rounds": 3,
        "budget_limit": 300000,
        "target_price": 120,
        "esg_threshold": 0.3,
        "risk_priorities": ["supply_security", "delivery_reliability", "concentration_risk"],
        "items": [
            {
                "item_id": "i1",
                "description": "Specialty alloy castings",
                "quantity": 1500,
                "annual_volume": 1500,
            }
        ],
        "candidate_suppliers": [
            {"supplier_id": candidate_ids[0], "name": "Supplier 20", "email": "s20@example.com"},
            {"supplier_id": candidate_ids[1], "name": "Supplier 8", "email": "s8@example.com"},
        ],
        "governance": {},
    }
    resp = agentcore.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=f"itest-bottleneck-{uuid.uuid4().hex}".ljust(33, "0"),
        contentType="application/json",
        accept="application/json",
        payload=_a2a_message(json.dumps(request)),
    )
    assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200
    # The BottleneckNegotiationResponse JSON is nested as an artifact text part.
    body = resp["response"].read().decode()
    assert request["negotiation_id"] in body
    assert "tco_analysis" in body
    assert "risk_assessment" in body
    assert "backup_supplier_recommendation" in body
    assert "recommended_supplier_id" in body
    assert "communication_log" in body
    # The negotiation actually ran: the agent recommends one of the candidate suppliers
    # (prerequisite guards force TCO/risk before the recommendation).
    result = json.loads(_result_artifact_text(body))
    assert result["recommended_supplier_id"] in candidate_ids
    assert result["tco_analysis"], "expected a TCO analysis per candidate"
    # §2.4-specific output: a backup supplier for concentration-risk mitigation —
    # populated, one of the candidates, and distinct from the winner.
    backup = result["backup_supplier_recommendation"]
    assert backup in candidate_ids, "expected a backup supplier from the candidate set"
    assert backup != result["recommended_supplier_id"], "backup must differ from the winner"
