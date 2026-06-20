"""Live end-to-end tests: PR → PO through the canonical event-driven chain.

The SKILL_MODE=live sibling to ../test_e2e_pr_to_po.py (PRD-013 Phase 6a /
REQ-TT400): POST a NON_CRITICAL requisition, let the canonical chain run — the app
writes the PR to the master store, whose DynamoDB Stream → pr_event_router →
ingest_pr starts the Step Functions negotiation — and assert a Purchase Order
surfaces at /api/orders with the requisition COMPLETED.

Two tests:

  test_pr_to_po_live               — basic canonical-chain smoke; may hit the quality
                                     gate and HITL if the bid composite is below the
                                     governance floor (correct behavior, not a bug).
  test_non_critical_auto_approve   — explicitly validates the auto-approve path by
                                     temporarily suppressing the quality floor (set to
                                     0.0 so any composite passes). Asserts no HITL
                                     pause occurred so the test fails fast if a future
                                     change accidentally re-introduces a gate.

Doubly opt-in: needs RUN_INTEGRATION=1 *and* RUN_INTEGRATION_INVOKE=1. Requires
the canonical chain deployed and the VPC NAT up (agents need Bedrock egress).
"""
import os

os.environ["SKILL_MODE"] = "live"
os.environ["AUTH_MODE"] = "dev"
os.environ.setdefault("ENV", "dev")

import json  # noqa: E402
import time  # noqa: E402
from uuid import NAMESPACE_DNS, uuid5  # noqa: E402

import boto3  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from test_tenant_app.auth.jwt import DEV_TENANT_ID  # noqa: E402
from test_tenant_app.main import app  # noqa: E402
from test_tenant_app.models import PurchaseOrder  # noqa: E402

from .conftest import REGION  # noqa: E402


def _execution_name(tenant_id: str, requisition_id: str) -> str:
    """The deterministic per-PR execution name the pr-event-router starts (mirrors
    mcp_servers/step_functions_orchestrator)."""
    return "neg-" + str(uuid5(NAMESPACE_DNS, f"{tenant_id}:negotiation:{requisition_id}"))

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_INVOKE") != "1",
    reason="set RUN_INTEGRATION_INVOKE=1 to run the real PR→PO DAG (billable, needs VPC)",
)

client = TestClient(app)
TENANT = DEV_TENANT_ID
ENV = os.getenv("ENV", "dev")

# A NON_CRITICAL item (category "Printing", supplier configured) — the auto-approve
# path, so the DAG runs to a PO without a human-approval pause.
_NON_CRITICAL_ITEM = "92f3123c-b678-583d-9c8e-004c2ee7126a"
_TERMINAL = {"SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"}


@pytest.fixture(scope="module", autouse=True)
def _state_machine_arn():
    """Resolve the deployed state machine ARN so the test can poll the execution
    the pr-event-router starts (by its deterministic name)."""
    sfn = boto3.client("stepfunctions", region_name=REGION)
    arn = next(
        (m["stateMachineArn"] for m in sfn.list_state_machines()["stateMachines"]
         if m["name"].startswith(f"{ENV}-buyer-team-procurement")),
        None,
    )
    if not arn:
        pytest.skip(f"no {ENV}-buyer-team-procurement state machine deployed")
    os.environ["STATE_MACHINE_ARN"] = arn
    return arn


def test_pr_to_po_live(_state_machine_arn):
    sfn = boto3.client("stepfunctions", region_name=REGION)

    payload = {
        "items": [{"item_id": _NON_CRITICAL_ITEM, "quantity": 2, "estimated_price": 50.0}],
        "delivery_address": "123 Live E2E St",
        "delivery_threshold_days": 14,
    }
    created = client.post("/api/requisitions", json=payload)
    assert created.status_code == 201, created.text
    rid = created.json()["requisition_id"]
    assert created.json()["status"] == "NEW"

    name = _execution_name(TENANT, rid)
    exec_arn = _state_machine_arn.replace(":stateMachine:", ":execution:") + f":{name}"

    status = None
    for _ in range(60):  # up to ~5 min; real agents + cold start
        try:
            status = sfn.describe_execution(executionArn=exec_arn)["status"]
        except sfn.exceptions.ExecutionDoesNotExist:
            # Eventual consistency: the master-store write → Stream → pr-event-router
            # → StartExecution chain takes a moment, so the execution may not exist on
            # the first polls. Keep waiting.
            status = None
        if status in _TERMINAL:
            break
        time.sleep(5)
    assert status == "SUCCEEDED", f"execution ended {status}"

    pr = client.get(f"/api/requisitions/{rid}").json()
    assert pr["status"] == "COMPLETED", pr

    orders = client.get("/api/orders").json()
    pos = [PurchaseOrder(**o) for o in orders if o["requisition_id"] == rid]
    assert len(pos) == 1
    assert pos[0].status == "ISSUED"
    assert pos[0].total_value > 0


@pytest.fixture
def suppress_quality_gate():
    """Temporarily set negotiation_quality_composite_minimum to 0.0.

    The bid_evaluation agent produces a real composite; if it falls below the
    governance floor (default 0.72) Node 6 pauses for HITL even on a NON_CRITICAL
    PR. Setting the floor to 0 means any composite passes, so the auto-approve path
    is exercised unconditionally. The prior value is restored on teardown.

    Mirrors the inverse of the `force_quality_gate` fixture in
    test_quality_gate_hitl_live.py.
    """
    ddb = boto3.resource("dynamodb", region_name=REGION)
    cfg_table = ddb.Table(f"{ENV}-system-config")
    key = {"config_group": "governance", "config_key": "default"}
    item = cfg_table.get_item(Key=key).get("Item") or {}
    cfg = json.loads(item["config_json"])
    prior = cfg.get("approval_thresholds", {}).get("negotiation_quality_composite_minimum")
    cfg.setdefault("approval_thresholds", {})["negotiation_quality_composite_minimum"] = 0.0
    cfg_table.update_item(
        Key=key, UpdateExpression="SET config_json = :j",
        ExpressionAttributeValues={":j": json.dumps(cfg)},
    )
    try:
        yield
    finally:
        item = cfg_table.get_item(Key=key).get("Item") or {}
        cfg = json.loads(item["config_json"])
        if prior is None:
            cfg.get("approval_thresholds", {}).pop("negotiation_quality_composite_minimum", None)
        else:
            cfg["approval_thresholds"]["negotiation_quality_composite_minimum"] = prior
        cfg_table.update_item(
            Key=key, UpdateExpression="SET config_json = :j",
            ExpressionAttributeValues={":j": json.dumps(cfg)},
        )


def test_non_critical_auto_approve(_state_machine_arn, suppress_quality_gate):
    """NON_CRITICAL PR runs end-to-end without any human-approval pause.

    The quality floor is suppressed (set to 0.0) so the bid_evaluation composite
    always passes Node 6's quality check. Combined with the item's low price (well
    under auto_approve_below_usd), the only path to SUCCEEDED is auto-approve.
    The test asserts that PENDING_APPROVAL is never reached so a future regression
    that re-introduces an unexpected gate fails explicitly.
    """
    sfn = boto3.client("stepfunctions", region_name=REGION)

    payload = {
        "items": [{"item_id": _NON_CRITICAL_ITEM, "quantity": 1, "estimated_price": 50.0}],
        "delivery_address": "123 Auto-Approve Test St",
        "delivery_threshold_days": 14,
    }
    created = client.post("/api/requisitions", json=payload)
    assert created.status_code == 201, created.text
    rid = created.json()["requisition_id"]

    name = _execution_name(TENANT, rid)
    exec_arn = _state_machine_arn.replace(":stateMachine:", ":execution:") + f":{name}"

    sfn_status = None
    for _ in range(60):
        try:
            desc = sfn.describe_execution(executionArn=exec_arn)
            sfn_status = desc["status"]
        except sfn.exceptions.ExecutionDoesNotExist:
            sfn_status = None
        if sfn_status in _TERMINAL:
            break
        time.sleep(5)

    assert sfn_status == "SUCCEEDED", f"execution ended {sfn_status}"

    # Assert the gate never paused: if PENDING_APPROVAL were ever set, the execution
    # would still be RUNNING when we check (not SUCCEEDED) — but make the intent explicit.
    neg_id = str(__import__("uuid").uuid5(NAMESPACE_DNS, f"{TENANT}:negotiation:{rid}"))
    neg = boto3.resource("dynamodb", region_name=REGION).Table(f"{ENV}-negotiations").get_item(
        Key={"tenant_id": TENANT, "negotiation_id": neg_id}
    ).get("Item") or {}
    assert neg.get("status") != "PENDING_APPROVAL", "unexpected HITL pause with suppressed quality gate"

    pr = client.get(f"/api/requisitions/{rid}").json()
    assert pr["status"] == "COMPLETED", pr

    orders = client.get("/api/orders").json()
    pos = [PurchaseOrder(**o) for o in orders if o["requisition_id"] == rid]
    assert len(pos) == 1
    assert pos[0].status == "ISSUED"
    assert pos[0].total_value > 0
