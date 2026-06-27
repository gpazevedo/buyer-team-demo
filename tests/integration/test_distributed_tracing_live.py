"""Live test: a PR→PO run stitches the orchestrator→agent hops into ONE X-Ray trace.

The tracing work (PR #44) was only validated offline with in-memory span exporters.
This test proves the real thing: AgentCore does not forward custom HTTP headers to the
container, so the W3C carrier rides inside the request body (`message.metadata`); the
agent extracts it and parents its spans under the orchestrator's `agentcore.invoke`
span. If propagation works, the orchestrator hop and the agent's `<agent>.execute`
span share a single trace_id. If it regresses, they land in two disconnected traces.

Correlation: `agentcore.invoke` is tagged with `procurement.negotiation_id`
(agent_invocation.py). After a real run we pull the window's traces from X-Ray, find
the one whose document mentions our negotiation_id (that's the orchestrator-rooted
trace), and assert the agent's `*.execute` span is present in the *same* trace — i.e.
the hop is stitched, not orphaned.

Doubly opt-in: needs RUN_INTEGRATION=1 *and* RUN_INTEGRATION_INVOKE=1. Requires the
canonical chain deployed, the VPC NAT up, and the ADOT layer exporting to X-Ray.
"""

import os

os.environ["SKILL_MODE"] = "live"
os.environ["AUTH_MODE"] = "dev"
os.environ.setdefault("ENV", "dev")

import json  # noqa: E402
import time  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from uuid import NAMESPACE_DNS, uuid5  # noqa: E402

import boto3  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from test_tenant_app.auth.jwt import DEV_TENANT_ID  # noqa: E402
from test_tenant_app.main import app  # noqa: E402

from .conftest import REGION  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_INVOKE") != "1",
    reason="set RUN_INTEGRATION_INVOKE=1 to run the real PR→PO DAG + X-Ray query (billable, needs VPC)",
)

client = TestClient(app)
TENANT = DEV_TENANT_ID
ENV = os.getenv("ENV", "dev")

# NON_CRITICAL item → routes through the spot_bidding agent, whose payload carries a
# negotiation_id, so the orchestrator hop is tagged and locatable.
_NON_CRITICAL_ITEM = "92f3123c-b678-583d-9c8e-004c2ee7126a"
_TERMINAL = {"SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"}

# X-Ray names: the orchestrator hop span and the agent-side span suffix.
_ORCH_SPAN = "agentcore.invoke"
_AGENT_SPAN_SUFFIX = ".execute"


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


def _segment_names(document: dict) -> set[str]:
    """All segment + (recursive) subsegment names in one X-Ray segment document."""
    names = {document.get("name", "")}
    for sub in document.get("subsegments", []) or []:
        names |= _segment_names(sub)
    return names


def _trace_docs(xray, trace_id: str) -> list[dict]:
    """The parsed segment documents for a single trace."""
    resp = xray.batch_get_traces(TraceIds=[trace_id])
    docs = []
    for trace in resp.get("Traces", []):
        for seg in trace.get("Segments", []):
            docs.append(json.loads(seg["Document"]))
    return docs


def _trace_ids_in_window(xray, start, end) -> list[str]:
    ids = []
    paginator = xray.get_paginator("get_trace_summaries")
    for page in paginator.paginate(StartTime=start, EndTime=end, TimeRangeType="TraceId"):
        ids.extend(s["Id"] for s in page.get("TraceSummaries", []))
    return ids


def test_pr_to_po_is_one_distributed_trace(_state_machine_arn):
    sfn = boto3.client("stepfunctions", region_name=REGION)
    xray = boto3.client("xray", region_name=REGION)

    t0 = datetime.now(timezone.utc)
    payload = {
        "items": [{"item_id": _NON_CRITICAL_ITEM, "quantity": 1, "estimated_price": 50.0}],
        "delivery_address": "123 Tracing Test St",
        "delivery_threshold_days": 14,
    }
    created = client.post("/api/requisitions", json=payload)
    assert created.status_code == 201, created.text
    rid = created.json()["requisition_id"]
    neg_id = str(uuid5(NAMESPACE_DNS, f"{TENANT}:negotiation:{rid}"))

    exec_arn = (
        _state_machine_arn.replace(":stateMachine:", ":execution:")
        + f":{_execution_name(TENANT, rid)}"
    )
    # The agent hops (spot_bidding/bid_evaluation `.execute`) all run BEFORE the human
    # ApprovalGate, which parks the run on a waitForTaskToken callback. So once the run
    # is terminal OR has reached ApprovalGate, the spans we assert on already exist — we
    # neither need nor (for a gated requisition) ever reach SUCCEEDED.
    def _progressed() -> bool:
        try:
            if sfn.describe_execution(executionArn=exec_arn)["status"] in _TERMINAL:
                return True
        except sfn.exceptions.ExecutionDoesNotExist:
            return False
        hist = sfn.get_execution_history(
            executionArn=exec_arn, maxResults=500, reverseOrder=True
        )
        return any(
            e.get("stateEnteredEventDetails", {}).get("name") == "ApprovalGate"
            for e in hist["events"]
        )

    progressed = False
    for _ in range(60):  # up to ~5 min; real agents + cold start
        if _progressed():
            progressed = True
            break
        time.sleep(5)
    assert progressed, "execution never reached the agent hops / approval gate"

    # Find the trace whose document mentions our negotiation_id, then assert the agent
    # hop is in that SAME trace. X-Ray ingestion lags the run, so poll for a bit.
    found = None
    for _ in range(9):  # up to ~3 min of ingestion lag
        time.sleep(20)
        window_end = datetime.now(timezone.utc) + timedelta(minutes=1)
        for trace_id in _trace_ids_in_window(xray, t0 - timedelta(minutes=1), window_end):
            docs = _trace_docs(xray, trace_id)
            blob = json.dumps(docs)
            if neg_id in blob:
                found = (trace_id, docs)
                break
        if found:
            break

    assert found, f"no X-Ray trace tagged with negotiation_id {neg_id} appeared"
    trace_id, docs = found

    all_names = set().union(*(_segment_names(d) for d in docs))
    assert _ORCH_SPAN in all_names, (
        f"trace {trace_id} has our negotiation_id but no {_ORCH_SPAN!r} hop: {sorted(all_names)}"
    )
    assert any(n.endswith(_AGENT_SPAN_SUFFIX) for n in all_names), (
        f"agent '*.execute' span is NOT in trace {trace_id} — the orchestrator→agent hop "
        f"is orphaned, not stitched: {sorted(all_names)}"
    )
