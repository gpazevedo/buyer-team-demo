"""Unit tests for GraphClient — the HITL approval callback payload.

Focus: the authenticated approver identity (Step 2) reaches Node 6's resume
Lambda so its effective-claims gate runs against a real identity, not a stub.
"""

import json

from test_tenant_app.clients import graph_client as gc


class _FakeLambda:
    def __init__(self):
        self.payload = None

    def invoke(self, FunctionName, InvocationType, Payload):  # noqa: N803 (boto3 kwargs)
        self.payload = json.loads(Payload)
        return {"Payload": _Body(b'{"status": "AWARDED"}')}


class _Body:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


def _capture(monkeypatch) -> _FakeLambda:
    fake = _FakeLambda()
    monkeypatch.setattr(gc, "SKILL_MODE", "live")
    monkeypatch.setattr(gc, "_lambda", lambda: fake)
    return fake


def test_approve_threads_real_approver(monkeypatch):
    fake = _capture(monkeypatch)
    approver = {"user_id": "user-abc", "tenant_id": "t-1", "claims": {"po_approve": "yes"}}
    gc.GraphClient().approve_award("t-1", "req-1", approver=approver)

    sent = fake.payload["approver"]
    assert fake.payload["decision"] == "APPROVED"
    assert sent["user_id"] == "user-abc"
    assert sent["tenant_id"] == "t-1"
    assert sent["claims"] == {"po_approve": "yes"}


def test_reject_threads_real_approver_and_reason(monkeypatch):
    fake = _capture(monkeypatch)
    approver = {"user_id": "user-xyz", "tenant_id": "t-2", "claims": {}}
    gc.GraphClient().reject_award("t-2", "req-2", reason="too expensive", approver=approver)

    assert fake.payload["decision"] == "REJECTED"
    assert fake.payload["reason"] == "too expensive"
    assert fake.payload["approver"]["user_id"] == "user-xyz"


def test_cycle_back_threads_real_approver(monkeypatch):
    fake = _capture(monkeypatch)
    approver = {"user_id": "user-cb", "tenant_id": "t-4", "claims": {}}
    gc.GraphClient().cycle_back_award("t-4", "req-4", approver=approver)

    assert fake.payload["decision"] == "CYCLE_BACK"
    assert fake.payload["approver"]["user_id"] == "user-cb"


def test_missing_approver_falls_back_to_system_identity(monkeypatch):
    """Non-interactive releases (e.g. tenant cancel) carry no human approver."""
    fake = _capture(monkeypatch)
    gc.GraphClient().reject_award("t-3", "req-3", reason="tenant_cancelled")

    sent = fake.payload["approver"]
    assert sent["user_id"] == "test-tenant-app"
    assert sent["tenant_id"] == "t-3"  # request tenant always stamped for the claims gate
