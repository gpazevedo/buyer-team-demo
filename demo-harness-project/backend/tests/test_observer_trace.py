"""`_trace_id_from_header` tests — parses DescribeExecution's `traceHeader`
into the X-Ray trace ID, replacing the old ±5min duration/start-time guess
against `get_trace_summaries` with a real lookup.
"""

import demo_harness.observer as observer
import pytest
from demo_harness.observer import _trace_id_from_header


def test_extracts_root_from_trace_header():
    header = "Root=1-5e1b4151-5ac6c58dc39e0d472dae5522;Parent=1234abcd1234abcd;Sampled=1"

    assert _trace_id_from_header(header) == "1-5e1b4151-5ac6c58dc39e0d472dae5522"


def test_handles_root_only_header():
    assert _trace_id_from_header("Root=1-5e1b4151-5ac6c58dc39e0d472dae5522") == (
        "1-5e1b4151-5ac6c58dc39e0d472dae5522"
    )


def test_returns_none_when_no_root_segment():
    assert _trace_id_from_header("Parent=1234abcd1234abcd;Sampled=1") is None


def test_returns_none_when_header_absent():
    assert _trace_id_from_header(None) is None


@pytest.mark.asyncio
async def test_cost_dashboard_url_is_always_present(monkeypatch):
    """cost_dashboard is negotiation-agnostic (env/region only) — must be
    returned even when SFN execution/trace resolution fails entirely."""
    monkeypatch.setattr(observer, "resolve_state_machine_arn", lambda: None)

    urls = await observer.get_trace_urls("neg-1")

    assert urls["cost_dashboard"] == (
        f"https://{observer.AWS_REGION}.console.aws.amazon.com"
        f"/cloudwatch/home?region={observer.AWS_REGION}"
        f"#dashboards:name={observer.ENV}-buyer-team-finops"
    )
    assert urls["sfn"] is None
    assert urls["xray"] is None
