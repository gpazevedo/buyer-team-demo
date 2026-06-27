"""Live end-to-end test: PR → PO for all four Kraljic quadrants.

Posts one Purchase Requisition per quadrant through the canonical event-driven chain
(master store → DynamoDB Stream → pr-event-router → SFN → agents → PO) and asserts
a PO is issued for each.

Quadrant→strategy→agent mapping exercised here:

  NON_CRITICAL → SPOT_BID         → spot_bidding           (auto-approve)
  LEVERAGE     → COMPETITIVE_AUCTION → leverage_auction     (may auto-approve or HITL)
  BOTTLENECK   → PARTNERSHIP_RISK → bottleneck_negotiation  (always HITL)
  STRATEGIC    → PARTNERSHIP_VALUE → strategic_partnership  (always HITL)

NON_CRITICAL uses the existing seeded item. The other three quadrants use dynamically
created items (seeded in a module fixture, deleted on teardown) so the test is
self-contained — it works on any tenant state without pre-seeded fixtures.

Doubly opt-in: needs RUN_INTEGRATION=1 *and* RUN_INTEGRATION_INVOKE=1. Requires
the VPC NAT to be up (agents need egress to Bedrock) and all 8 AgentCore runtimes READY.
"""

from __future__ import annotations

import os

os.environ["SKILL_MODE"] = "live"
os.environ["AUTH_MODE"] = "dev"
os.environ.setdefault("ENV", "dev")

import time  # noqa: E402
from decimal import Decimal  # noqa: E402
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
    reason="set RUN_INTEGRATION_INVOKE=1 to run (billable, needs VPC + all 8 runtimes READY)",
)

client = TestClient(app)
TENANT = DEV_TENANT_ID
ENV = os.getenv("ENV", "dev")
_TERMINAL = {"SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"}

# Existing seeded item — NON_CRITICAL (Printing, 743371c0 category).
_NON_CRITICAL_ITEM = "92f3123c-b678-583d-9c8e-004c2ee7126a"

# One well-known category per non-NON_CRITICAL quadrant, each with a supplier configured.
_CAT = {
    "LEVERAGE": "8c5c8667-9749-5b32-995b-825e463e9af7",  # IT Services
    "BOTTLENECK": "4bc6a266-3565-5982-9616-bd6d9bda22a1",  # Logistics
    "STRATEGIC": "6491a8c6-6418-53fc-9892-9790587d61b7",  # Maintenance
}

# Quadrants that always trigger HITL regardless of price/quality.
_ALWAYS_HITL = {"BOTTLENECK", "STRATEGIC"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _execution_name(requisition_id: str) -> str:
    return "neg-" + str(uuid5(NAMESPACE_DNS, f"{TENANT}:negotiation:{requisition_id}"))


def _negotiation_id(requisition_id: str) -> str:
    return str(uuid5(NAMESPACE_DNS, f"{TENANT}:negotiation:{requisition_id}"))


def _ddb_table(suffix: str):
    return boto3.resource("dynamodb", region_name=REGION).Table(f"{ENV}-{suffix}")


def _neg(negotiation_id: str) -> dict:
    return (
        _ddb_table("negotiations")
        .get_item(Key={"tenant_id": TENANT, "negotiation_id": negotiation_id})
        .get("Item")
        or {}
    )


def _item_id(quadrant: str) -> str:
    """Deterministic per-quadrant item id so reruns don't duplicate rows."""
    return str(uuid5(NAMESPACE_DNS, f"e2e-quadrant-test:{quadrant}"))


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def state_machine_arn():
    sfn = boto3.client("stepfunctions", region_name=REGION)
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


@pytest.fixture(scope="module")
def test_items():
    """Seed one test item per non-NON_CRITICAL quadrant; delete them on teardown.

    Items are keyed by deterministic item_id (idempotent on rerun) and carry a
    modest unit price so the budget_limit is low and LEVERAGE may auto-approve.
    """
    table = _ddb_table("items")
    items = {
        q: {
            "tenant_id": TENANT,
            "item_id": _item_id(q),
            "category_id": cat_id,
            "name": f"e2e-test {q.lower()} item",
            "estimated_unit_price": Decimal("80"),
            "annual_volume": 1,
            "currency": "USD",
        }
        for q, cat_id in _CAT.items()
    }
    for item in items.values():
        table.put_item(Item=item)
    yield items
    for item in items.values():
        table.delete_item(Key={"tenant_id": TENANT, "item_id": item["item_id"]})


# ---------------------------------------------------------------------------
# core run helper
# ---------------------------------------------------------------------------


def _run_pr_to_po(state_machine_arn: str, item_id: str, quadrant: str) -> None:
    """Post a PR, drive the SFN DAG to completion, assert PO ISSUED.

    Handles both the auto-approve and HITL paths: the first time the negotiation
    reaches PENDING_APPROVAL the test calls POST /approve. For BOTTLENECK and
    STRATEGIC this is mandatory (the gate always pauses); for LEVERAGE it may or
    may not happen — either path is valid.
    """
    sfn = boto3.client("stepfunctions", region_name=REGION)

    resp = client.post(
        "/api/requisitions",
        json={
            "items": [{"item_id": item_id, "quantity": 1, "estimated_price": 80.0}],
            "delivery_address": f"1 E2E Test St ({quadrant})",
            "delivery_threshold_days": 30,
        },
    )
    assert resp.status_code == 201, f"[{quadrant}] create PR failed: {resp.text}"
    rid = resp.json()["requisition_id"]
    neg_id = _negotiation_id(rid)
    exec_arn = (
        state_machine_arn.replace(":stateMachine:", ":execution:") + f":{_execution_name(rid)}"
    )

    sfn_status = None
    approved = False

    for _ in range(90):  # up to ~7.5 min; multi-round agent loops + cold-start
        neg = _neg(neg_id)

        if (
            not approved
            and neg.get("status") == "PENDING_APPROVAL"
            and neg.get("approval_task_token")
        ):
            ar = client.post(f"/api/requisitions/{rid}/approve")
            assert ar.status_code == 200, f"[{quadrant}] approve failed: {ar.text}"
            approved = True

        try:
            sfn_status = sfn.describe_execution(executionArn=exec_arn)["status"]
        except sfn.exceptions.ExecutionDoesNotExist:
            sfn_status = None  # eventually-consistent: router may not have fired yet

        if sfn_status in _TERMINAL:
            break
        time.sleep(5)

    assert sfn_status == "SUCCEEDED", (
        f"[{quadrant}] execution ended {sfn_status!r} (neg status={_neg(neg_id).get('status')})"
    )

    if quadrant in _ALWAYS_HITL:
        assert approved, f"[{quadrant}] expected HITL approval pause but gate never triggered"

    pr = client.get(f"/api/requisitions/{rid}").json()
    assert pr["status"] == "COMPLETED", f"[{quadrant}] PR not completed: {pr}"

    orders = client.get("/api/orders").json()
    pos = [PurchaseOrder(**o) for o in orders if o["requisition_id"] == rid]
    assert pos, f"[{quadrant}] no PO found for requisition {rid}"
    assert pos[0].status == "ISSUED", f"[{quadrant}] PO status: {pos[0].status}"
    assert pos[0].total_value > 0, f"[{quadrant}] PO has zero total_value"


# ---------------------------------------------------------------------------
# tests — one per quadrant
# ---------------------------------------------------------------------------


def test_non_critical_pr_to_po(state_machine_arn):
    """NON_CRITICAL → SPOT_BID (spot_bidding agent) → auto-approve → PO ISSUED."""
    _run_pr_to_po(state_machine_arn, _NON_CRITICAL_ITEM, "NON_CRITICAL")


def test_leverage_pr_to_po(state_machine_arn, test_items):
    """LEVERAGE → COMPETITIVE_AUCTION (leverage_auction agent) → PO ISSUED."""
    _run_pr_to_po(state_machine_arn, test_items["LEVERAGE"]["item_id"], "LEVERAGE")


def test_bottleneck_pr_to_po(state_machine_arn, test_items):
    """BOTTLENECK → PARTNERSHIP_RISK (bottleneck_negotiation agent) → HITL → PO ISSUED."""
    _run_pr_to_po(state_machine_arn, test_items["BOTTLENECK"]["item_id"], "BOTTLENECK")


def test_strategic_pr_to_po(state_machine_arn, test_items):
    """STRATEGIC → PARTNERSHIP_VALUE (strategic_partnership agent) → HITL → PO ISSUED."""
    _run_pr_to_po(state_machine_arn, test_items["STRATEGIC"]["item_id"], "STRATEGIC")
