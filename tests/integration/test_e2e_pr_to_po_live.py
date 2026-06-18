"""Live end-to-end test: PR → PO through the canonical event-driven chain.

The SKILL_MODE=live sibling to ../test_e2e_pr_to_po.py (PRD-013 Phase 6a /
REQ-TT400): POST a NON_CRITICAL requisition, let the canonical chain run — the app
writes the PR to the master store, whose DynamoDB Stream → pr_event_router →
ingest_pr starts the Step Functions negotiation — and assert a Purchase Order
surfaces at /api/orders with the requisition COMPLETED.

Doubly opt-in: needs RUN_INTEGRATION=1 *and* RUN_INTEGRATION_INVOKE=1. It needs the
canonical chain deployed (master store stream + pr-event-router) and starts a real
execution that invokes the agent runtimes (billable, cold-start-prone, and requires
the VPC NAT to be up so the private subnets have egress).
"""
import os

os.environ["SKILL_MODE"] = "live"
os.environ["AUTH_MODE"] = "dev"
os.environ.setdefault("ENV", "dev")

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
