"""Live test: A2A retry exhaustion does not double-handle via a DLQ escalation
(PRD-006 §2.1/§2.7, REQ-R451, WS1 fix — impl v1.0.30).

Closes the "no A2A-exhaustion DLQ message on dev" validation gate from the WS1
plan without deploying to Lambda or touching the VPC: drives the REAL resilience
wrapper (`resilience.agent_invocation.invoke_agent_runtime`) against a REAL but
nonexistent AgentCore runtime ARN, same pattern as test_circuit_breaker_live.py,
so each call is a genuine boto `InvokeAgentRuntime` `ClientError` — a real retry
exhaustion, not a mock.

`build_retry_decorator("a2a_agent_call")` is called by `agent_invocation.py` with
the default `dlq_on_exhaustion=False`, so `retry_error_callback` is unattached and
`resilience.dlq.publish_dlq_message` must never fire on this path — the exception
alone propagates to the caller (the agent-node executor's fallback). Spies on
`publish_dlq_message` at the real call site (`resilience.retry._publish_to_dlq`
imports it lazily from `resilience.dlq`, so patching the module attribute there is
what the (would-be) callback would actually resolve).

Deterministic and deploy-independent: exercises the local `orchestrator/resilience`
source directly (whatever is checked out), not the deployed Lambda build artifact —
proves the source fix works against real AWS failures, independent of whether the
Lambda zip has been rebuilt/applied yet.

Opt-in via RUN_INTEGRATION=1; needs AWS creds (control-plane reachable), no VPC/NAT.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("ENV", "dev")
os.environ.setdefault("AWS_REGION", "us-east-1")

# impl/orchestrator on sys.path → top-level `resilience`, as the deployed executors import it.
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "orchestrator"))
import pybreaker  # noqa: E402
from resilience.agent_invocation import invoke_agent_runtime  # noqa: E402
from resilience.circuit_breaker import get_a2a_breaker  # noqa: E402
from resilience.config import config  # noqa: E402

from .conftest import ACCOUNT_ID, REGION, TENANT  # noqa: E402

# Well-formed but nonexistent runtime ARN → a real InvokeAgentRuntime ClientError per call.
_BOGUS_ARN = (
    f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:runtime/dev_nonexistent_dlq_live-000000"
)


@pytest.fixture
def fresh_a2a_breaker():
    """Reset the process-local a2a breaker to CLOSED/zeroed before and after the test."""
    breaker = get_a2a_breaker()
    breaker.close()
    yield breaker
    breaker.close()


def test_a2a_exhaustion_reraises_without_dlq_escalation(fresh_a2a_breaker):
    breaker = fresh_a2a_breaker
    fail_max = config.get("circuit_breaker_config.a2a_agent.fail_max")
    assert isinstance(fail_max, int) and fail_max >= 2, fail_max

    # Stay strictly under fail_max so every call is a genuine retry-exhaustion
    # failure through the CLOSED breaker, not a fast-fail from an OPEN one.
    calls = fail_max - 1

    with patch("resilience.dlq.publish_dlq_message") as spy:
        for i in range(calls):
            with pytest.raises(Exception) as excinfo:
                invoke_agent_runtime(
                    agent_name="dlq-live-test",
                    tenant_id=TENANT,
                    agent_runtime_arn=_BOGUS_ARN,
                    payload={"tenant_id": TENANT, "negotiation_id": "dlq-live"},
                )
            assert not isinstance(excinfo.value, pybreaker.CircuitBreakerError), (
                f"call {i + 1}/{calls} fast-failed on an already-open breaker; "
                f"lower `calls` or re-check fail_max — this call must be a real exhaustion"
            )

    assert str(breaker.current_state) == pybreaker.STATE_CLOSED, (
        "breaker tripped before exhausting `calls` — reduce `calls` relative to fail_max"
    )
    spy.assert_not_called()
