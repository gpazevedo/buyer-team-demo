"""Control-plane checks: the 8 Buyer Team agent runtimes exist and are READY.

This verifies deployment/provisioning, not agent behaviour. Behaviour requires
real (non-placeholder) container images — see test_agentcore_invoke.py.
"""

from .conftest import EXPECTED_RUNTIMES


def _runtimes(client) -> dict[str, str]:
    out: dict[str, str] = {}
    paginator_args = {}
    while True:
        resp = client.list_agent_runtimes(**paginator_args)
        for r in resp.get("agentRuntimes", []):
            out[r["agentRuntimeName"]] = r["status"]
        token = resp.get("nextToken")
        if not token:
            return out
        paginator_args = {"nextToken": token}


def test_all_expected_runtimes_present(agentcore_control):
    names = set(_runtimes(agentcore_control))
    missing = EXPECTED_RUNTIMES - names
    assert not missing, f"missing runtimes: {sorted(missing)}"


def test_all_runtimes_ready(agentcore_control):
    statuses = _runtimes(agentcore_control)
    not_ready = {n: statuses[n] for n in EXPECTED_RUNTIMES if statuses.get(n) != "READY"}
    assert not not_ready, f"runtimes not READY: {not_ready}"
