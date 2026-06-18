"""Live end-to-end test: PR → PO through the deployed Step Functions DAG.

The SKILL_MODE=live sibling to ../test_e2e_pr_to_po.py (PRD-013 Phase 6a /
REQ-TT400): POST a NON_CRITICAL requisition, let the real orchestrator run the
negotiation, and assert a Purchase Order surfaces at /api/orders with the
requisition COMPLETED.

Doubly opt-in: needs RUN_INTEGRATION=1 *and* RUN_INTEGRATION_INVOKE=1 — it starts
a real execution that invokes the agent runtimes (billable, cold-start-prone, and
requires the VPC NAT to be up so the private subnets have egress).
"""
import os

os.environ["SKILL_MODE"] = "live"
os.environ["AUTH_MODE"] = "dev"
os.environ.setdefault("ENV", "dev")

import time  # noqa: E402

import boto3  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from test_tenant_app.auth.jwt import DEV_TENANT_ID  # noqa: E402
from test_tenant_app.clients.graph_client import _execution_name  # noqa: E402
from test_tenant_app.main import app  # noqa: E402
from test_tenant_app.models import PurchaseOrder  # noqa: E402

from .conftest import REGION  # noqa: E402

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
    """Resolve and export the deployed state machine ARN (graph_client reads
    STATE_MACHINE_ARN at call time)."""
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
        status = sfn.describe_execution(executionArn=exec_arn)["status"]
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
