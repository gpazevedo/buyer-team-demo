"""`_sfn_execution_graph` tests — parses the type-specific SFN execution-history
events (TaskStateEntered/Exited, ChoiceStateEntered/Exited, …) into a
pending/running/succeeded/failed status per canonical state. Fake SFN client,
no AWS involved.
"""

import demo_harness.observer as observer
from demo_harness.observer import SFN_GRAPH_STATES, _sfn_execution_graph


class _FakeSfnClient:
    def __init__(self, status, events):
        self._status = status
        self._events = events
        self.describe_calls = []
        self.history_calls = []

    def describe_execution(self, executionArn):
        self.describe_calls.append(executionArn)
        return {"status": self._status, "executionArn": executionArn}

    def get_execution_history(self, executionArn, maxResults=100, nextToken=None):
        self.history_calls.append((executionArn, maxResults, nextToken))
        return {"events": self._events}


def _entered(name, kind="Task"):
    # boto3 normalizes type-specific state events into the generic
    # stateEnteredEventDetails key regardless of the Task/Choice/… prefix.
    return {"type": f"{kind}StateEntered", "stateEnteredEventDetails": {"name": name}}


def _exited(name, kind="Task"):
    return {"type": f"{kind}StateExited", "stateExitedEventDetails": {"name": name}}


def _status_by_name(graph):
    return {s["name"]: s["status"] for s in graph["states"]}


def test_running_marks_last_entered_state_in_progress(monkeypatch):
    events = [
        _entered("IngestValidate"),
        _exited("IngestValidate"),
        _entered("CheckIngest", "Choice"),
        _exited("CheckIngest", "Choice"),
        _entered("KraljicClassify"),
    ]
    monkeypatch.setattr(observer, "_sfn_client", _FakeSfnClient("RUNNING", events))

    graph = _sfn_execution_graph("exec-arn")

    assert graph["execution_status"] == "RUNNING"
    statuses = _status_by_name(graph)
    assert statuses["IngestValidate"] == "succeeded"
    assert statuses["KraljicClassify"] == "running"
    assert statuses["StrategyExecute"] == "pending"


def test_succeeded_marks_all_canonical_states_succeeded(monkeypatch):
    events = []
    for name in SFN_GRAPH_STATES:
        events.append(_entered(name))
        events.append(_exited(name))
    monkeypatch.setattr(observer, "_sfn_client", _FakeSfnClient("SUCCEEDED", events))

    graph = _sfn_execution_graph("exec-arn")

    assert graph["execution_status"] == "SUCCEEDED"
    assert [s["name"] for s in graph["states"]] == SFN_GRAPH_STATES
    assert all(s["status"] == "succeeded" for s in graph["states"])


def test_empty_history_marks_everything_pending(monkeypatch):
    monkeypatch.setattr(observer, "_sfn_client", _FakeSfnClient("RUNNING", []))

    graph = _sfn_execution_graph("exec-arn")

    assert all(s["status"] == "pending" for s in graph["states"])


def test_non_canonical_states_are_ignored(monkeypatch):
    events = [
        _entered("Terminated", "Succeed"),
        _exited("Terminated", "Succeed"),
    ]
    monkeypatch.setattr(observer, "_sfn_client", _FakeSfnClient("SUCCEEDED", events))

    graph = _sfn_execution_graph("exec-arn")

    assert [s["name"] for s in graph["states"]] == SFN_GRAPH_STATES
    assert all(s["status"] == "pending" for s in graph["states"])


def test_failed_execution_marks_unexited_state_failed(monkeypatch):
    events = [
        _entered("IngestValidate"),
        _exited("IngestValidate"),
        _entered("KraljicClassify"),
    ]
    monkeypatch.setattr(observer, "_sfn_client", _FakeSfnClient("FAILED", events))

    graph = _sfn_execution_graph("exec-arn")

    assert _status_by_name(graph)["KraljicClassify"] == "failed"
