"""Unit tests for skill_client — session affinity, SSE parsing, error handling."""

import json
import os

import pytest

os.environ.setdefault("SKILL_MODE", "stub")
os.environ.setdefault("ENV", "dev")

from test_tenant_app.clients.skill_client import _invoke_skill_tool  # noqa: E402


def _sse(result: dict) -> bytes:
    envelope = {"result": {"content": [{"type": "text", "text": json.dumps(result)}]}}
    return f"data: {json.dumps(envelope)}\n\n".encode()


def _sse_error(code: int, message: str) -> bytes:
    return f'data: {{"jsonrpc":"2.0","error":{{"code":{code},"message":"{message}"}}}}\n\n'.encode()


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body


def _arm(monkeypatch, response: bytes):
    monkeypatch.setattr(
        "test_tenant_app.clients.skill_client._skill_runtime_arn",
        lambda: "arn:aws:fake",
    )
    client = _FakeClient(response)
    monkeypatch.setattr(
        "test_tenant_app.clients.skill_client._runtime_client",
        lambda: client,
    )
    monkeypatch.setattr(
        "test_tenant_app.clients.skill_client._tracer.start_as_current_span",
        lambda *a, **kw: _FakeSpan(),
    )
    return client


class _FakeSpan:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class _FakeClient:
    def __init__(self, response: bytes):
        self._response = response
        self.last_kwargs = None

    def invoke_agent_runtime(self, **kwargs):
        self.last_kwargs = kwargs
        return {"response": _FakeResponse(self._response)}


def test_passes_runtime_session_id(monkeypatch):
    """_invoke_skill_tool must pass runtimeSessionId so AgentCore reuses warm microVMs."""
    client = _arm(monkeypatch, _sse({"valid": True, "issues": []}))

    _invoke_skill_tool("validate_datasets", {"tenant_id": "t1"})

    kwargs = client.last_kwargs
    assert kwargs is not None, "invoke_agent_runtime was not called"
    assert "runtimeSessionId" in kwargs, "runtimeSessionId missing from invoke_agent_runtime"
    assert kwargs["runtimeSessionId"] == "skill-keepalive-test_tenant".ljust(33, "0")


def test_stable_session_id_across_calls(monkeypatch):
    """Two calls must reuse the same runtimeSessionId, not mint a fresh one per call."""
    client = _arm(monkeypatch, _sse({"valid": True, "issues": []}))

    _invoke_skill_tool("validate_datasets", {"tenant_id": "t1"})
    first = client.last_kwargs["runtimeSessionId"]

    _invoke_skill_tool("validate_datasets", {"tenant_id": "t1"})
    second = client.last_kwargs["runtimeSessionId"]

    assert first == second, (
        f"session ID changed across calls: {first!r} → {second!r} "
        "(must be stable for session affinity)"
    )


def test_extracts_result_from_sse(monkeypatch):
    _arm(monkeypatch, _sse({"valid": False, "issues": [{"dataset": "kraljic", "errors": ["bad"]}]}))

    result = _invoke_skill_tool("validate_datasets", {"tenant_id": "t1"})

    assert result == {"valid": False, "issues": [{"dataset": "kraljic", "errors": ["bad"]}]}


def test_raises_on_sse_error(monkeypatch):
    _arm(monkeypatch, _sse_error(-32010, "runtime start failed"))

    with pytest.raises(RuntimeError, match="runtime start failed"):
        _invoke_skill_tool("validate_datasets", {"tenant_id": "t1"})


def test_raises_on_empty_response(monkeypatch):
    _arm(monkeypatch, b"")

    with pytest.raises(RuntimeError, match="No result from skill tool"):
        _invoke_skill_tool("validate_datasets", {"tenant_id": "t1"})
