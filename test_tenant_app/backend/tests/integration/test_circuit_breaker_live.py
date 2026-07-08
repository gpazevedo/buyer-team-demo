"""Live test: the A2A circuit breaker opens under real failures (PRD-006 §2.2, REQ-R100..R103).

Closes the "breaker never tripped live" gap. The offline suite proves the breaker logic
against a fake failing callable; this drives the REAL resilience wrapper
(`resilience.agent_invocation.invoke_agent_runtime`) against a REAL but nonexistent
AgentCore runtime ARN, so every attempt is a genuine boto `InvokeAgentRuntime` failure —
counted by the REAL `a2a_agent` breaker built from LIVE governance config
(`circuit_breaker_config.a2a_agent.fail_max`, read from `dev-system-config`).

After `fail_max` real failures the breaker transitions to OPEN; the next call must
fast-fail with `pybreaker.CircuitBreakerError` *without* making a boto call at all — the
whole point of the breaker (stop hammering a down dependency). We assert both: the state
is OPEN, and the fast-fail is a CircuitBreakerError (not another underlying ClientError).

Deterministic and deploy-independent: the failures are real ClientErrors (ResourceNotFound
/ Validation / AccessDenied on a bogus ARN), so no warm-container luck or fault stub is
needed. Not billable — the ARN doesn't exist, so no agent ever runs. The breaker is a
process-local singleton, so the test resets it before and after to avoid cross-test leak.

Opt-in via RUN_INTEGRATION=1; needs AWS creds (control-plane reachable), no VPC/NAT.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

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
    f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:runtime/dev_nonexistent_breaker_live-000000"
)


@pytest.fixture
def fresh_a2a_breaker():
    """Reset the process-local a2a breaker to CLOSED/zeroed before and after the test."""
    breaker = get_a2a_breaker()
    breaker.close()
    yield breaker
    breaker.close()


def test_a2a_breaker_opens_after_fail_max_real_failures(fresh_a2a_breaker):
    breaker = fresh_a2a_breaker
    fail_max = config.get("circuit_breaker_config.a2a_agent.fail_max")
    assert isinstance(fail_max, int) and fail_max >= 1, fail_max
    assert str(breaker.current_state) == pybreaker.STATE_CLOSED

    # Drive REAL failures through the real wrapper (bogus ARN → boto ClientError). pybreaker
    # trips on the fail_max-th failure, so the breaker must reach OPEN within fail_max calls.
    opened_at = None
    for i in range(fail_max):
        try:
            invoke_agent_runtime(
                agent_name="breaker-live-test",
                tenant_id=TENANT,
                agent_runtime_arn=_BOGUS_ARN,
                payload={"tenant_id": TENANT, "negotiation_id": "breaker-live"},
            )
        except Exception:  # noqa: BLE001 — ResourceNotFound family, or the CB-open on the tripping call
            pass
        if str(breaker.current_state) == pybreaker.STATE_OPEN:
            opened_at = i + 1
            break

    assert opened_at == fail_max, (
        f"breaker opened at call {opened_at}, expected exactly fail_max={fail_max}"
    )
    assert str(breaker.current_state) == pybreaker.STATE_OPEN

    # The next call fast-fails as a CircuitBreakerError WITHOUT touching boto (open circuit).
    t0 = time.monotonic()
    with pytest.raises(pybreaker.CircuitBreakerError):
        invoke_agent_runtime(
            agent_name="breaker-live-test",
            tenant_id=TENANT,
            agent_runtime_arn=_BOGUS_ARN,
            payload={"tenant_id": TENANT, "negotiation_id": "breaker-live"},
        )
    # Fast-fail is immediate (no network round-trip to InvokeAgentRuntime).
    assert time.monotonic() - t0 < 1.0, "open-circuit call was not a fast fast-fail"
