"""Is Buyer Team actually reachable? Exercises the real /demo/health route
(Node 6 approval-gate Lambda + master-store/requisitions DynamoDB tables +
Step Functions state machine), not a mocked stand-in — a red result here
means the harness cannot drive the real orchestrator, not that a test
assertion is wrong.
"""

from __future__ import annotations

import os

os.environ.setdefault("SKILL_MODE", "live")
os.environ.setdefault("ENV", "dev")

from demo_harness.main import app
from fastapi.testclient import TestClient

EXPECTED_CHECKS = {
    "approval_gate_lambda",
    "master_store_table",
    "requisitions_table",
    "step_functions",
}


def test_buyer_team_is_reachable():
    with TestClient(app) as client:
        res = client.get("/demo/health")

    assert res.status_code == 200
    body = res.json()
    assert set(body["checks"]) == EXPECTED_CHECKS
    assert body["healthy"] is True, f"Buyer Team unreachable: {body['checks']}"
    assert body["pricing_mode"] in {"live", "fallback", "unknown"}, (
        f"Unexpected pricing_mode: {body.get('pricing_mode')}"
    )
    assert "pricing_mode_source" in body
