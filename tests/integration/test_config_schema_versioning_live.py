"""Live test: the config schema-version gate blocks a forward-incompatible config.

The offline suite (`orchestrator/tests/test_config_schema.py`) proves `migrate()`
in isolation, but the gate has never been exercised against the live hot-reload
path — both readers (`graph_common._fetch_config`, `resilience.config.read_system_config`)
pull `version` off the real `{env}-system-config` item per request. This test
publishes a forward-incompatible `governance` version live and asserts a PR→PO run
fails fast instead of silently feeding an unrecognised config shape into the nodes.

Mechanism: the running code understands `governance` schema major v1 (see
`config_schema._CURRENT_MAJOR`). Stamping the live `governance/default` item with
`version="2.0"` makes the next governance read raise `ConfigVersionIncompatible`.
That unhandled exception hits the state machine's `States.ALL` catch → the `Failed`
state, so the execution ends FAILED with the cause recorded in its history — and no
PO is ever issued. The prior version is restored on teardown.

Doubly opt-in: needs RUN_INTEGRATION=1 *and* RUN_INTEGRATION_INVOKE=1. Requires the
canonical chain deployed and the VPC NAT up (the run reaches a governance-reading node).
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

from .conftest import REGION  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_INVOKE") != "1",
    reason="set RUN_INTEGRATION_INVOKE=1 to run the real config-gate DAG (billable, needs VPC)",
)

client = TestClient(app)
TENANT = DEV_TENANT_ID
ENV = os.getenv("ENV", "dev")

# A NON_CRITICAL item — the same fixture the e2e live test uses. The quadrant is
# irrelevant here: the run must die at the first governance read, well before a PO.
_NON_CRITICAL_ITEM = "92f3123c-b678-583d-9c8e-004c2ee7126a"
_TERMINAL = {"SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"}


def _execution_name(tenant_id: str, requisition_id: str) -> str:
    return "neg-" + str(uuid5(NAMESPACE_DNS, f"{tenant_id}:negotiation:{requisition_id}"))


@pytest.fixture(scope="module", autouse=True)
def _state_machine_arn():
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


@pytest.fixture
def forward_incompatible_governance():
    """Stamp the live `governance/default` item with a forward-incompatible major.

    Only the `version` attribute changes — `config_json` is left untouched, so the
    sole reason a read fails is the schema gate (not a malformed payload). The prior
    version is restored on teardown. Mirrors the mutate/restore pattern of
    `suppress_quality_gate` in test_e2e_pr_to_po_live.py.
    """
    ddb = boto3.resource("dynamodb", region_name=REGION)
    cfg_table = ddb.Table(f"{ENV}-system-config")
    key = {"config_group": "governance", "config_key": "default"}
    item = cfg_table.get_item(Key=key).get("Item")
    if not item:
        pytest.skip("no governance/default system-config item to gate")
    prior_version = item.get("version")
    cfg_table.update_item(
        Key=key,
        UpdateExpression="SET version = :v",
        ExpressionAttributeValues={":v": "2.0"},
    )
    try:
        yield
    finally:
        if prior_version is None:
            cfg_table.update_item(Key=key, UpdateExpression="REMOVE version")
        else:
            cfg_table.update_item(
                Key=key,
                UpdateExpression="SET version = :v",
                ExpressionAttributeValues={":v": prior_version},
            )


def _execution_failed_with_config_gate(sfn, exec_arn: str) -> bool:
    """True if the execution history records ConfigVersionIncompatible as the cause."""
    paginator = sfn.get_paginator("get_execution_history")
    for page in paginator.paginate(executionArn=exec_arn, reverseOrder=True):
        for event in page["events"]:
            for detail_key in (
                "taskFailedEventDetails",
                "executionFailedEventDetails",
                "lambdaFunctionFailedEventDetails",
            ):
                detail = event.get(detail_key)
                if detail and "ConfigVersionIncompatible" in (
                    detail.get("cause", "") + detail.get("error", "")
                ):
                    return True
    return False


def test_forward_incompatible_governance_blocks_run(
    _state_machine_arn, forward_incompatible_governance
):
    """A forward-incompatible governance version fails the run fast — no PO issued.

    Proves the live gate: the execution ends FAILED, its history names
    ConfigVersionIncompatible (so it's the gate, not an unrelated failure), the
    requisition never reaches COMPLETED, and no Purchase Order is cut.
    """
    sfn = boto3.client("stepfunctions", region_name=REGION)

    payload = {
        "items": [{"item_id": _NON_CRITICAL_ITEM, "quantity": 1, "estimated_price": 50.0}],
        "delivery_address": "123 Config Gate Test St",
        "delivery_threshold_days": 14,
    }
    created = client.post("/api/requisitions", json=payload)
    assert created.status_code == 201, created.text
    rid = created.json()["requisition_id"]

    name = _execution_name(TENANT, rid)
    exec_arn = _state_machine_arn.replace(":stateMachine:", ":execution:") + f":{name}"

    status = None
    for _ in range(60):  # up to ~5 min; eventual-consistency on the Stream→router→start chain
        try:
            status = sfn.describe_execution(executionArn=exec_arn)["status"]
        except sfn.exceptions.ExecutionDoesNotExist:
            status = None
        if status in _TERMINAL:
            break
        time.sleep(5)

    assert status == "FAILED", f"expected fail-fast, execution ended {status}"
    assert _execution_failed_with_config_gate(sfn, exec_arn), (
        "execution failed but not via the config schema gate — "
        "ConfigVersionIncompatible not found in the execution history"
    )

    pr = client.get(f"/api/requisitions/{rid}").json()
    assert pr["status"] != "COMPLETED", pr

    orders = client.get("/api/orders").json()
    assert not [o for o in orders if o["requisition_id"] == rid], "no PO should be issued"
