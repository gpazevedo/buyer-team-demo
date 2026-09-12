"""`/demo/negotiations/{id}/audit` tests — reads the append-only
`{env}-negotiation-events` trail for one negotiation and parses each row's
`detail` JSON string. Fake DynamoDB table, no AWS involved.

`source_layer` is the load-bearing field: it separates decisions made by an
orchestrator node Lambda (`lambda_core`) from those made by a Bedrock AgentCore
agent (`buyer_agent_core`).
"""

import json

import pytest
import test_tenant_app.clients.ddb as ddb
from demo_harness.observer import get_audit_trail


class _FakeTable:
    def __init__(self, items):
        self._items = items
        self.queries = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return {"Items": list(self._items)}


@pytest.fixture
def fake_table(monkeypatch):
    def _install(items):
        table = _FakeTable(items)
        seen = {}

        def _table(name):
            seen["name"] = name
            return table

        monkeypatch.setattr(ddb, "table", _table)
        return table, seen

    return _install


def _row(ts, actor, event_type, source_layer, detail):
    return {
        "ts": ts,
        "event_id": f"evt-{ts}",
        "actor": actor,
        "event_type": event_type,
        "source_layer": source_layer,
        "detail": json.dumps(detail),
    }


NODE_ROW = _row(
    "2026-09-04T22:40:47Z",
    "orchestrator.node.ingest_validate",
    "node_decision",
    "lambda_core",
    {"negotiation_id": "n-1", "quadrant": "STRATEGIC"},
)
AGENT_ROW = _row(
    "2026-09-04T22:41:18Z",
    "agent:strategic-partnership-agent",
    "tool_call",
    "buyer_agent_core",
    {"input": {"negotiation_id": "n-1"}, "result": "ok"},
)


def test_reads_the_negotiation_events_table_partitioned_by_tenant(fake_table):
    _, seen = fake_table([NODE_ROW])

    get_audit_trail("n-1")

    assert seen["name"] == "negotiation-events"


def test_parses_each_detail_json_string_into_an_object(fake_table):
    fake_table([NODE_ROW])

    events = get_audit_trail("n-1")["events"]

    assert events[0]["detail"] == {"negotiation_id": "n-1", "quadrant": "STRATEGIC"}


def test_reports_both_runtimes_that_produced_decisions(fake_table):
    fake_table([NODE_ROW, AGENT_ROW])

    body = get_audit_trail("n-1")

    assert [e["source_layer"] for e in body["events"]] == ["lambda_core", "buyer_agent_core"]
    assert [e["actor"] for e in body["events"]] == [
        "orchestrator.node.ingest_validate",
        "agent:strategic-partnership-agent",
    ]
    assert body["count"] == 2


def test_agent_tier_reached_is_true_when_an_agent_row_exists(fake_table):
    fake_table([NODE_ROW, AGENT_ROW])

    assert get_audit_trail("n-1")["agent_tier_reached"] is True


def test_agent_tier_reached_is_false_with_nat_down(fake_table):
    """Fallback mode: the agent tier never runs, so only node rows are written.
    The panel needs to say so rather than look broken."""
    fake_table([NODE_ROW])

    assert get_audit_trail("n-1")["agent_tier_reached"] is False


def test_unparseable_detail_is_preserved_rather_than_dropped(fake_table):
    fake_table([{**NODE_ROW, "detail": "{not valid json"}])

    events = get_audit_trail("n-1")["events"]

    assert events[0]["detail"] == {"raw": "{not valid json"}


def test_empty_trail_returns_an_empty_list(fake_table):
    """A negotiation that ran before the audit trail shipped has no rows."""
    fake_table([])

    body = get_audit_trail("n-1")

    assert body["count"] == 0
    assert body["events"] == []
    assert body["agent_tier_reached"] is False
