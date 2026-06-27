"""Live test: saga compensation undoes real PR→PO side effects on the dev tables.

The offline suite (`orchestrator/tests/test_compensation.py`) proves the compensation
map, LIFO replay, and each undo handler against in-memory fakes. This test exercises the
*same* `resilience.compensation` code in-process but against the live `dev-*` tables —
the durable `dev-saga-log` plus the four side-effect tables the PR→PO flow moves money
and suppliers on (`dev-orders`, `dev-negotiations`, `dev-awards`, `dev-requisitions`).

It is deploy-independent: it imports the module the node executors import and drives it
directly, so it validates the real DynamoDB schema/conditional-update semantics today,
regardless of the node-zip deploy lag affecting the DAG-level tests. It is NOT billable —
no agent invokes, no Step Functions execution; only a handful of DDB writes under a
synthetic, self-cleaning negotiation id.

Flow: seed one ISSUED PO, one award, one AWARDED negotiation, one COMPLETED requisition
under a throwaway negotiation id; record the five compensations the §3.7 handlers would;
run `compensate_negotiation` and assert every side effect was undone on the live rows;
then run it a second time and assert it is a pure no-op (all entries skipped) — the
idempotency the whole saga relies on. All seeded rows + saga entries are deleted on
teardown.

Opt-in via RUN_INTEGRATION=1 (enforced by the parent conftest). Needs AWS credentials;
no VPC/NAT and no RUN_INTEGRATION_INVOKE required.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

os.environ.setdefault("ENV", "dev")
os.environ.setdefault("AWS_REGION", "us-east-1")

# Import the resilience package the deployed node executors import (impl/orchestrator
# on sys.path → top-level `resilience`), the same shape orchestrator/tests/conftest uses.
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "orchestrator"))
from resilience import compensation as comp  # noqa: E402

from .conftest import REGION, TENANT  # noqa: E402

ENV = os.getenv("ENV", "dev")


def _t(suffix: str):
    """A live dev table by the same env-var/default rule the handlers use."""
    import boto3

    return boto3.resource("dynamodb", region_name=REGION).Table(f"{ENV}-{suffix}")


@pytest.fixture
def seeded():
    """Seed the four side-effect rows for a throwaway negotiation, yield its ids, clean up.

    Ids are uuids so they never collide with real data and teardown is unambiguous: the
    seeded rows are deleted and every `comp#` entry under the negotiation's saga pk is
    purged.
    """
    neg = f"saga-live-{uuid.uuid4()}"
    order_id = f"po-{uuid.uuid4()}"
    award_id = f"aw-{uuid.uuid4()}"
    req_id = f"req-{uuid.uuid4()}"
    pk_neg = f"{TENANT}#{neg}"

    _t("orders").put_item(
        Item={
            "pk": f"{TENANT}#{order_id}",
            "sk": "metadata",
            "tenant_id": TENANT,
            "order_id": order_id,
            "negotiation_id": neg,
            "status": "ISSUED",
        }
    )
    _t("awards").put_item(
        Item={
            "tenant_id": TENANT,
            "award_id": award_id,
            "negotiation_id": neg,
            "approval_status": "AUTO_APPROVED",
        }
    )
    _t("negotiations").put_item(
        Item={
            "tenant_id": TENANT,
            "negotiation_id": neg,
            "status": "AWARDED",
        }
    )
    _t("requisitions").put_item(
        Item={
            "pk": f"{TENANT}#{req_id}",
            "sk": "metadata",
            "tenant_id": TENANT,
            "requisition_id": req_id,
            "status": "COMPLETED",
        }
    )

    ids = {"neg": neg, "order_id": order_id, "award_id": award_id, "req_id": req_id}
    try:
        yield ids
    finally:
        _t("orders").delete_item(Key={"pk": f"{TENANT}#{order_id}", "sk": "metadata"})
        _t("awards").delete_item(Key={"tenant_id": TENANT, "award_id": award_id})
        _t("negotiations").delete_item(Key={"tenant_id": TENANT, "negotiation_id": neg})
        _t("requisitions").delete_item(Key={"pk": f"{TENANT}#{req_id}", "sk": "metadata"})
        saga = comp._saga_table()
        for entry in comp._query_steps(pk_neg):
            saga.delete_item(Key={"pk": pk_neg, "sk": entry["sk"]})


def _record_all(ids: dict, supplier_id: str = "supplier-x") -> None:
    """Record the five compensations in the order the PR→PO flow would commit them.

    LIFO replay then undoes them newest-first (PO cancel first). The record order is
    realistic but the assertions don't depend on it — the four rows are independent.
    """
    neg = ids["neg"]
    comp.record_compensation(
        TENANT,
        neg,
        "withdraw_bid_invitations",
        {"supplier_ids": [supplier_id], "reason": "test_abandon"},
    )
    comp.record_compensation(
        TENANT, neg, "cancel_award", {"award_id": ids["award_id"], "reason": "test_abandon"}
    )
    comp.record_compensation(
        TENANT, neg, "revert_negotiation_award", {"requisition_id": ids["req_id"]}
    )
    comp.record_compensation(
        TENANT,
        neg,
        "notify_award_retraction",
        {"supplier_id": supplier_id, "reason": "test_abandon"},
    )
    comp.record_compensation(
        TENANT,
        neg,
        "cancel_purchase_order",
        {"order_id": ids["order_id"], "reason": "test_abandon"},
    )


def test_compensate_undoes_every_side_effect_live(seeded):
    """compensate_negotiation flips all four live side-effect rows and reports 5 undone."""
    _record_all(seeded)
    neg = seeded["neg"]

    result = comp.compensate_negotiation(TENANT, neg, reason="live_test")
    assert result == {"compensated": 5, "skipped": 0, "failed": 0}, result

    order = _t("orders").get_item(Key={"pk": f"{TENANT}#{seeded['order_id']}", "sk": "metadata"})[
        "Item"
    ]
    assert order["status"] == "CANCELLED"

    negot = _t("negotiations").get_item(Key={"tenant_id": TENANT, "negotiation_id": neg})["Item"]
    assert negot["status"] == "CANCELLED"
    assert negot["award_retracted"] is True
    assert negot["bids_withdrawn"] is True

    req = _t("requisitions").get_item(Key={"pk": f"{TENANT}#{seeded['req_id']}", "sk": "metadata"})[
        "Item"
    ]
    assert req["status"] == "CANCELLED"

    award = _t("awards").get_item(Key={"tenant_id": TENANT, "award_id": seeded["award_id"]})["Item"]
    assert award["cancelled"] is True


def test_compensate_is_idempotent_live(seeded):
    """A second sweep is a pure no-op: every entry already COMPENSATED → all skipped.

    Proves the idempotency the saga depends on against the live conditional updates — the
    second run must not re-flip rows or re-run handlers (5 skipped, 0 compensated).
    """
    _record_all(seeded)
    neg = seeded["neg"]

    first = comp.compensate_negotiation(TENANT, neg, reason="live_test_1")
    assert first == {"compensated": 5, "skipped": 0, "failed": 0}, first

    second = comp.compensate_negotiation(TENANT, neg, reason="live_test_2")
    assert second == {"compensated": 0, "skipped": 5, "failed": 0}, second

    # Rows still in their undone terminal state — the no-op didn't disturb them.
    order = _t("orders").get_item(Key={"pk": f"{TENANT}#{seeded['order_id']}", "sk": "metadata"})[
        "Item"
    ]
    assert order["status"] == "CANCELLED"
    negot = _t("negotiations").get_item(Key={"tenant_id": TENANT, "negotiation_id": neg})["Item"]
    assert negot["status"] == "CANCELLED"


def test_compensate_handles_missing_side_effect_row_live(seeded):
    """A PO that was never written (or already cancelled) compensates as a no-op, not a fail.

    Deletes the seeded order before the sweep: the conditional `status = ISSUED` undo finds
    nothing to flip and is swallowed, so the entry still counts as compensated (the undo's
    postcondition — PO not ISSUED — already holds). The other four still apply.
    """
    _record_all(seeded)
    neg = seeded["neg"]
    _t("orders").delete_item(Key={"pk": f"{TENANT}#{seeded['order_id']}", "sk": "metadata"})

    result = comp.compensate_negotiation(TENANT, neg, reason="live_test_missing")
    assert result == {"compensated": 5, "skipped": 0, "failed": 0}, result

    negot = _t("negotiations").get_item(Key={"tenant_id": TENANT, "negotiation_id": neg})["Item"]
    assert negot["status"] == "CANCELLED"
