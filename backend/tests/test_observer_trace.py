"""`_trace_id_from_header` tests — parses DescribeExecution's `traceHeader`
into the X-Ray trace ID, replacing the old ±5min duration/start-time guess
against `get_trace_summaries` with a real lookup.
"""

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
