"""Unit tests for `MasterDataClient.list_prs`'s live-mode row handling.

A partial/orphaned requisition row (e.g. a status-only write that never landed the
full row) has no `requisition_id`. `_synthesize_graph_nodes` indexes
`pr["requisition_id"]` unconditionally, so one such row used to raise `KeyError` and
500 the entire `/demo/requisitions` response — the frontend's fetch then silently
swallowed the non-JSON error response and rendered "No purchase requisitions yet"
even when other, well-formed requisitions existed.
"""

from test_tenant_app.clients import ddb
from test_tenant_app.clients import dynamo_client as dynamo_client_module
from test_tenant_app.clients import master_data_client as master_data_client_module
from test_tenant_app.clients.master_data_client import MasterDataClient


class _FakeTable:
    def __init__(self, items):
        self._items = items

    def scan(self, FilterExpression=None):
        return {"Items": self._items}


def test_list_prs_skips_rows_missing_requisition_id(monkeypatch):
    monkeypatch.setattr(master_data_client_module, "SKILL_MODE", "live")
    good_row = {
        "pk": "tenant-1#req-good",
        "sk": "metadata",
        "requisition_id": "req-good",
        "tenant_id": "tenant-1",
        "status": "COMPLETED",
        "created_at": "2026-01-01T00:00:00+00:00",
        "items": [],
    }
    malformed_row = {
        "pk": "tenant-1#req-bad",
        "sk": "metadata",
        "status": "PENDING_HUMAN_APPROVAL",
    }
    monkeypatch.setattr(ddb, "table", lambda name: _FakeTable([good_row, malformed_row]))
    monkeypatch.setattr(dynamo_client_module.dynamo_client, "get_negotiations", lambda *a, **kw: [])

    out = MasterDataClient().list_prs("tenant-1")

    assert [pr["requisition_id"] for pr in out] == ["req-good"]


def test_list_prs_returns_all_rows_when_all_well_formed(monkeypatch):
    monkeypatch.setattr(master_data_client_module, "SKILL_MODE", "live")
    rows = [
        {
            "pk": "tenant-1#req-a",
            "sk": "metadata",
            "requisition_id": "req-a",
            "tenant_id": "tenant-1",
            "status": "COMPLETED",
            "created_at": "2026-01-01T00:00:00+00:00",
            "items": [],
        },
        {
            "pk": "tenant-1#req-b",
            "sk": "metadata",
            "requisition_id": "req-b",
            "tenant_id": "tenant-1",
            "status": "COMPLETED",
            "created_at": "2026-01-02T00:00:00+00:00",
            "items": [],
        },
    ]
    monkeypatch.setattr(ddb, "table", lambda name: _FakeTable(rows))
    monkeypatch.setattr(dynamo_client_module.dynamo_client, "get_negotiations", lambda *a, **kw: [])

    out = MasterDataClient().list_prs("tenant-1")

    assert {pr["requisition_id"] for pr in out} == {"req-a", "req-b"}
