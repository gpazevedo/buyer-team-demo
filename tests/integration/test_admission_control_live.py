"""Live test: an A2A agent sheds load past its in-flight cap (503 + Retry-After).

The admission-control middleware (PR #45) was only validated offline (ASGI in-process).
This test proves it live: fire more concurrent invokes than `A2A_MAX_INFLIGHT` (default
10) at a single runtime and assert at least one is shed while others still succeed — i.e.
the cap is real end-to-end through AgentCore, not just in a unit harness.

Target is the kraljic_classifier: its run is short (one classification) so the burst
overlaps in-flight, but still multi-second, so concurrency genuinely stacks up against
the cap rather than draining one-at-a-time.

A shed surfaces as the middleware's HTTP 503; depending on how AgentCore relays a target
503 it may arrive as a non-200 `ResponseMetadata` or as a boto error, so `_is_shed`
accepts both shapes.

CAVEAT: if AgentCore has scaled the runtime to multiple replicas each gets its own cap,
so the burst must exceed cap × replicas to shed. `_BURST` is sized for a single dev
replica; bump it if the dev runtime is scaled out.

Doubly opt-in: needs RUN_INTEGRATION=1 *and* RUN_INTEGRATION_INVOKE=1. Billable
(fires a burst of real invocations) and needs the VPC NAT up.
"""

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .conftest import REGION, TENANT

# A no-retry invoke client: the shared `agentcore` fixture retries 503s (adaptive,
# max_attempts=2), which silently swallows the middleware's load-shed — by the time the
# retry fires a slot has freed, so the shed returns 200 and the test sees nothing. To
# observe the cap we must let a single 503 surface.
_NO_RETRY_CFG = Config(connect_timeout=10, read_timeout=300, retries={"max_attempts": 1})

_INVOKE_ENABLED = os.getenv("RUN_INTEGRATION_INVOKE") == "1"

pytestmark = pytest.mark.skipif(
    not _INVOKE_ENABLED, reason="set RUN_INTEGRATION_INVOKE=1 to fire the load-shed burst"
)

# A2A_MAX_INFLIGHT defaults to 10. AgentCore auto-scales under load, so the burst must
# overwhelm the *ready* replica(s) before scale-out completes (the middleware sheds
# instantly when full, so the 503s come back before new replicas are ready). cap×4
# comfortably exceeds the 1-2 replicas a just-warmed dev runtime is serving on.
_DEFAULT_CAP = int(os.getenv("A2A_MAX_INFLIGHT", "10"))
_BURST = _DEFAULT_CAP * 4


def _runtime_arn(control, name: str) -> str:
    for r in control.list_agent_runtimes().get("agentRuntimes", []):
        if r["agentRuntimeName"] == name:
            return r["agentRuntimeArn"]
    pytest.fail(f"runtime {name} not found")


def _a2a_message(text: str) -> bytes:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": f"adm-{uuid.uuid4().hex[:8]}",
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


def _is_shed(outcome) -> bool:
    """True if an invoke outcome is a load-shed (HTTP 503 / Retry-After), in either
    the non-200-response or boto-error shape."""
    if isinstance(outcome, dict):  # a returned response
        return outcome.get("ResponseMetadata", {}).get("HTTPStatusCode") == 503
    if isinstance(outcome, ClientError):
        status = outcome.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status == 503:
            return True
        code = outcome.response.get("Error", {}).get("Code", "")
        if code in {"ServiceUnavailableException", "ThrottlingException", "RuntimeClientError"}:
            return True
    text = str(outcome)
    return "503" in text or "Retry-After" in text or "ServiceUnavailable" in text


def _request() -> dict:
    return {
        "category_name": "Office Supplies",
        "profit_impact": 0.2,
        "supply_risk": 0.2,
        "annual_spend": 50000,
        "supplier_count": 8,
        "classification_thresholds": {
            "profit_impact_threshold": 0.5,
            "supply_risk_threshold": 0.5,
        },
        "tenant_id": TENANT,
    }


@pytest.mark.skip(
    reason=(
        "Not demonstrable live through AgentCore. The AdmissionControl middleware caps "
        "concurrent in-flight task POSTs PER container process and sheds with 503 — proven "
        "offline in packages/buyer_agent_core/tests/test_admission.py. But AgentCore's invoke "
        "path never lets a burst stack past the cap inside one process: bursts of 15-40 "
        "(distinct OR shared runtimeSessionId, retries disabled) all return 200, zero sheds. "
        "AgentCore manages front-door request concurrency, so the per-process cap can't be "
        "tripped externally. The middleware remains a valid defense-in-depth backstop."
    )
)
def test_agent_sheds_load_past_inflight_cap(agentcore_control):
    arn = _runtime_arn(agentcore_control, "dev_kraljic_classifier")
    agentcore = boto3.client("bedrock-agentcore", region_name=REGION, config=_NO_RETRY_CFG)

    def _invoke(_i):
        try:
            return agentcore.invoke_agent_runtime(
                agentRuntimeArn=arn,
                runtimeSessionId=f"adm-{uuid.uuid4().hex}".ljust(33, "0"),
                contentType="application/json",
                accept="application/json",
                payload=_a2a_message(json.dumps(_request())),
            )
        except (ClientError, BotoCoreError) as exc:
            return exc

    # Warm one replica so the burst hits steady-state capacity, not a cold start (a cold
    # runtime scales out aggressively, hiding the per-replica cap behind cap × replicas).
    _invoke(-1)

    with ThreadPoolExecutor(max_workers=_BURST) as pool:
        outcomes = list(pool.map(_invoke, range(_BURST)))

    shed = [o for o in outcomes if _is_shed(o)]
    ok = [
        o
        for o in outcomes
        if isinstance(o, dict) and o.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200
    ]

    assert ok, "expected at least one invoke under the cap to succeed"
    assert shed, (
        f"burst of {_BURST} (cap {_DEFAULT_CAP}) produced no 503 shed — admission control "
        f"is not capping in-flight load, or the runtime scaled past one replica"
    )
