"""`/demo/metrics` tests — builds SEARCH() expressions over each metric's full
dimension schema and reduces the returned series to one number per tile. Fake
CloudWatch client, no AWS involved.

The empty case matters most: a demo opens with no data, and a metric that hasn't
been published yet must read as None (rendered "—"), never as a real 0.
"""

import demo_harness.health as health
import demo_harness.observer as observer
import pytest
from demo_harness.observer import (
    _METRIC_SPECS,
    _reduce_series,
    _search_expression,
    get_live_metrics,
)
from fastapi import HTTPException


class _FakeCloudWatchClient:
    def __init__(self, results=None, raises=None):
        self._results = results if results is not None else []
        self._raises = raises
        self.calls = []

    def get_metric_data(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises:
            raise self._raises
        return {"MetricDataResults": self._results}


@pytest.fixture
def fake_cw(monkeypatch):
    def _install(results=None, raises=None):
        client = _FakeCloudWatchClient(results, raises)
        # observer imports the client inside the request handler, so patching the
        # health module is what the endpoint actually picks up.
        monkeypatch.setattr(health, "_cloudwatch_client", client)
        return client

    return _install


# ── expression building ───────────────────────────────────────────


def test_search_expression_carries_full_dimension_schema():
    """A partial dimension list returns zero datapoints with no error, so the
    SEARCH schema must list every dimension the emitter publishes."""
    spec = next(s for s in _METRIC_SPECS if s["key"] == "negotiations_started")
    expr = _search_expression(spec, "tenant-abc")

    assert "{procurement/business,kraljic_quadrant,strategy,tenant_id}" in expr
    assert 'MetricName="negotiation.started"' in expr
    assert 'tenant_id="tenant-abc"' in expr


def test_search_expression_collapses_series_to_one_line():
    """SEARCH returns one series per dimension combination; without an outer
    SUM/AVG the result is many lines rather than a single tile value."""
    for spec in _METRIC_SPECS:
        expr = _search_expression(spec, "t")
        assert expr.startswith(("SUM(SEARCH(", "AVG(SEARCH("))


def test_every_spec_is_scoped_to_the_tenant():
    for spec in _METRIC_SPECS:
        assert 'tenant_id="t"' in _search_expression(spec, "t")


# ── series reduction ──────────────────────────────────────────────


def test_absent_metric_reduces_to_none_not_zero():
    assert _reduce_series([], "sum") is None
    assert _reduce_series([], "latest") is None


def test_sum_reduction_totals_the_window():
    assert _reduce_series([1.0, 2.0, 3.0], "sum") == 6.0


def test_latest_reduction_takes_the_newest_point():
    # results are requested ScanBy=TimestampDescending, so index 0 is newest
    assert _reduce_series([9.0, 5.0, 1.0], "latest") == 9.0


# ── endpoint ──────────────────────────────────────────────────────


def test_returns_a_tile_per_spec_with_namespace_and_dashboard(fake_cw):
    fake_cw([{"Id": f"m{i}", "Values": [2.0]} for i in range(len(_METRIC_SPECS))])

    body = get_live_metrics()

    assert body["window_minutes"] == observer.METRICS_WINDOW_MINUTES
    assert set(body["metrics"]) == {s["key"] for s in _METRIC_SPECS}
    for spec in _METRIC_SPECS:
        tile = body["metrics"][spec["key"]]
        assert tile["namespace"] == spec["namespace"]
        assert tile["label"] == spec["label"]
        assert tile["dashboard_url"].startswith("https://")


def test_empty_cloudwatch_response_yields_all_none(fake_cw):
    """The state a demo opens in — every tile blank, nothing reading as 0."""
    fake_cw([])

    metrics = get_live_metrics()["metrics"]

    assert all(t["value"] is None for t in metrics.values())


def test_one_metric_present_leaves_the_others_none(fake_cw):
    fake_cw([{"Id": "m0", "Values": [1.0, 1.0]}])

    metrics = get_live_metrics()["metrics"]

    assert metrics[_METRIC_SPECS[0]["key"]]["value"] == 2.0
    assert all(metrics[s["key"]]["value"] is None for s in _METRIC_SPECS[1:])


def test_queries_one_batched_call_over_the_window(fake_cw):
    client = fake_cw([])

    get_live_metrics()

    assert len(client.calls) == 1
    call = client.calls[0]
    assert len(call["MetricDataQueries"]) == len(_METRIC_SPECS)
    assert call["ScanBy"] == "TimestampDescending"
    window = call["EndTime"] - call["StartTime"]
    assert window.total_seconds() == observer.METRICS_WINDOW_MINUTES * 60


def test_cloudwatch_failure_surfaces_as_503(fake_cw):
    fake_cw(raises=RuntimeError("throttled"))

    with pytest.raises(HTTPException) as exc:
        get_live_metrics()

    assert exc.value.status_code == 503
