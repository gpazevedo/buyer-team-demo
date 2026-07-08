"""build_pr — validates the quadrant, submits via master_data_client.create_pr
(mocked here, not the real DynamoDB-backed client), and derives a deterministic
negotiation_id so the caller can start watching it before the orchestrator
picks up the PR event.
"""

from uuid import NAMESPACE_DNS, uuid5

import pytest
from demo_harness.config import TENANT_ID
from demo_harness.pr_generator import build_pr


def test_rejects_unknown_quadrant():
    with pytest.raises(ValueError, match="Unknown quadrant"):
        build_pr("NOT_A_QUADRANT")


def test_quadrant_is_case_insensitive(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "demo_harness.pr_generator.master_data_client.create_pr",
        lambda **kwargs: calls.append(kwargs) or {"requisition_id": "req-1"},
    )

    pr = build_pr("non_critical")

    assert pr["quadrant"] == "NON_CRITICAL"
    assert len(calls) == 1


def test_submits_pr_and_returns_expected_shape(monkeypatch):
    captured = {}

    def fake_create_pr(**kwargs):
        captured.update(kwargs)
        return {"requisition_id": "req-123"}

    monkeypatch.setattr("demo_harness.pr_generator.master_data_client.create_pr", fake_create_pr)

    pr = build_pr("LEVERAGE", quantity=3)

    # Submitted with the right tenant/items/budget
    assert captured["tenant_id"] == TENANT_ID
    assert captured["items"][0]["sku"] == "BJ-32-MWTIRE"
    assert captured["items"][0]["quantity"] == 3
    assert captured["budget_limit"] == 2400 * 3
    assert "correlation_id" in captured

    # Returned shape
    assert pr["requisition_id"] == "req-123"
    assert pr["quadrant"] == "LEVERAGE"
    assert pr["item"]["sku"] == "BJ-32-MWTIRE"
    assert pr["item"]["quantity"] == 3
    assert pr["tenant_id"] == TENANT_ID

    # negotiation_id is the deterministic uuid5 derived from the requisition_id
    expected_negotiation_id = str(uuid5(NAMESPACE_DNS, f"{TENANT_ID}:negotiation:req-123"))
    assert pr["negotiation_id"] == expected_negotiation_id


def test_default_quantity_is_one(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "demo_harness.pr_generator.master_data_client.create_pr",
        lambda **kwargs: captured.update(kwargs) or {"requisition_id": "req-1"},
    )

    pr = build_pr("STRATEGIC")

    assert captured["items"][0]["quantity"] == 1
    assert pr["item"]["quantity"] == 1
