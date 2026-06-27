"""Live validation: the 0.72 quality gate (REQ-G203c) + the HITL approve path.

Drives the full production path through the canonical chain and the app's approve
endpoint, exercising the two paths that the auto-approve e2e never reaches:

  1. Quality gate (REQ-G203c) — Node 5's `bid_evaluation` agent emits a real winning
     composite; Node 6 blocks auto-approval (regardless of amount) when it is below
     `governance.approval_thresholds.negotiation_quality_composite_minimum`. To make
     this deterministic with a live agent, the floor is temporarily raised to 0.99 so
     any real composite trips `below_quality_minimum`. The PR's amount stays well under
     `auto_approve_below_usd`, so price is provably not the blocker.
  2. HITL approve — the gate pauses on `waitForTaskToken`; the app releases it via
     `POST /api/requisitions/{id}/approve` → `master_data_client.approve_pr` →
     `graph_client.approve_award` → Node 6 `resume_approval` (APPROVED) → Node 7 → PO.

Doubly opt-in (RUN_INTEGRATION=1 + RUN_INTEGRATION_INVOKE=1): billable, needs the
canonical chain deployed and the VPC NAT up (agents reachable).
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

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_INVOKE") != "1",
    reason="set RUN_INTEGRATION_INVOKE=1 to run the real quality-gate/HITL DAG (billable, needs VPC)",
)

client = TestClient(app)
TENANT = DEV_TENANT_ID
ENV = os.getenv("ENV", "dev")

# Same NON_CRITICAL item the auto-approve e2e uses; low amount keeps it under the
# auto_approve_below_usd ceiling so the quality gate is the only possible blocker.
_NON_CRITICAL_ITEM = "92f3123c-b678-583d-9c8e-004c2ee7126a"
_TERMINAL = {"SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"}
_FORCED_MINIMUM = 0.99


def _negotiation_id(requisition_id: str) -> str:
    return str(uuid5(NAMESPACE_DNS, f"{TENANT}:negotiation:{requisition_id}"))


def _execution_name(requisition_id: str) -> str:
    return "neg-" + _negotiation_id(requisition_id)


@pytest.fixture(scope="module")
def sfn():
    return boto3.client("stepfunctions", region_name=REGION)


@pytest.fixture(scope="module")
def state_machine_arn(sfn):
    arn = next(
        (
            m["stateMachineArn"]
            for m in sfn.list_state_machines()["stateMachines"]
            if m["name"].startswith(f"{ENV}-buyer-team-procurement")
        ),
        None,
    )
    if not arn:
        pytest.skip(f"no {ENV}-buyer-team-procurement state machine deployed")
    os.environ["STATE_MACHINE_ARN"] = arn
    return arn


@pytest.fixture(scope="module", autouse=True)
def force_quality_gate():
    """Temporarily raise the composite quality floor so any real winning composite
    trips the gate. Read-modify-write of the governance config_json blob so every
    other section (incl. the PRD-006 resilience blocks) is preserved."""
    ddb = boto3.resource("dynamodb", region_name=REGION)
    cfg_table = ddb.Table(f"{ENV}-system-config")
    key = {"config_group": "governance", "config_key": "default"}
    item = cfg_table.get_item(Key=key).get("Item") or {}
    cfg = json.loads(item["config_json"])
    prior = cfg.get("approval_thresholds", {}).get("negotiation_quality_composite_minimum")
    cfg.setdefault("approval_thresholds", {})["negotiation_quality_composite_minimum"] = (
        _FORCED_MINIMUM
    )
    cfg_table.update_item(
        Key=key,
        UpdateExpression="SET config_json = :j",
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
            Key=key,
            UpdateExpression="SET config_json = :j",
            ExpressionAttributeValues={":j": json.dumps(cfg)},
        )


def _ddb_table(suffix: str):
    return boto3.resource("dynamodb", region_name=REGION).Table(f"{ENV}-{suffix}")


def _neg(negotiation_id: str) -> dict:
    return (
        _ddb_table("negotiations")
        .get_item(Key={"tenant_id": TENANT, "negotiation_id": negotiation_id})
        .get("Item")
        or {}
    )


def _award(award_id: str) -> dict:
    return (
        _ddb_table("awards").get_item(Key={"tenant_id": TENANT, "award_id": award_id}).get("Item")
        or {}
    )


def test_quality_gate_blocks_then_app_approves(sfn, state_machine_arn):
    payload = {
        "items": [{"item_id": _NON_CRITICAL_ITEM, "quantity": 2, "estimated_price": 50.0}],
        "delivery_address": "123 Quality Gate St",
        "delivery_threshold_days": 14,
    }
    created = client.post("/api/requisitions", json=payload)
    assert created.status_code == 201, created.text
    rid = created.json()["requisition_id"]

    negotiation_id = _negotiation_id(rid)
    exec_arn = (
        state_machine_arn.replace(":stateMachine:", ":execution:") + f":{_execution_name(rid)}"
    )

    # 1. The gate must PAUSE (not auto-approve). If the agent falls back (no composite)
    #    the price-only path would auto-approve — surface that as a clear skip cause.
    neg = {}
    for _ in range(60):  # ~5 min: canonical-chain lag + cold-start agents
        neg = _neg(negotiation_id)
        if neg.get("status") == "PENDING_APPROVAL" and neg.get("approval_task_token"):
            break
        try:
            if sfn.describe_execution(executionArn=exec_arn)["status"] in _TERMINAL:
                pytest.fail(
                    "execution reached terminal without pausing — Node 5 likely produced "
                    "no composite (agent fallback); rerun with warm agents"
                )
        except sfn.exceptions.ExecutionDoesNotExist:
            pass
        time.sleep(5)
    assert neg.get("status") == "PENDING_APPROVAL", f"gate did not pause: {neg.get('status')}"
    assert sfn.describe_execution(executionArn=exec_arn)["status"] == "RUNNING"

    # 2. Prove it blocked on quality, not amount. The gate's block-reason chain is
    #    human_required → quadrant → quality → price; with human_required false, a
    #    NON_CRITICAL quadrant, and the award amount under the auto-approve ceiling,
    #    the only branch that can pause is `below_quality_minimum`. (Node 6 logs
    #    block_reason at INFO, which the Lambda root logger drops, so assert the
    #    structural conditions rather than scraping the log.)
    award = _award(neg["award_id"])
    assert award.get("composite_score") is not None, "Node 5 produced no composite to gate on"
    assert float(award["composite_score"]) < _FORCED_MINIMUM
    assert float(award["awarded_price"]) <= 10000, "amount must be under the auto-approve ceiling"
    assert (neg.get("kraljic_quadrant") or "") not in {"BOTTLENECK", "STRATEGIC"}
    assert not neg.get("human_approval_required")

    # 3. Release through the APP path: POST /approve → graph_client.approve_award.
    approved = client.post(f"/api/requisitions/{rid}/approve")
    assert approved.status_code == 200, approved.text

    # 4. The token-resumed workflow runs Node 7 to a PO.
    status = None
    for _ in range(60):
        try:
            status = sfn.describe_execution(executionArn=exec_arn)["status"]
        except sfn.exceptions.ExecutionDoesNotExist:
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
