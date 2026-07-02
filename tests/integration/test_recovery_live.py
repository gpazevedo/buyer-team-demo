"""Live test: the recovery-owned primitives against the dev tables (PRD-006 §4.2/4.3).

Closes two "recovery never exercised live" gaps:

  1. REQ-R502 concurrent-recovery lock — `acquire_recovery_lock` grants a
     per-(tenant, negotiation) lock, refuses a second holder while unexpired, and
     lets a new holder take over once `expires_at` has passed. Proven against the
     live `dev-recovery-locks` table (its real conditional-put semantics), not fakes.

  2. §4.3 total-timeout → §4.4 compensate → CANCELLED — the branch `run_recovery_flow`
     takes for a negotiation past its total-timeout ceiling: `is_negotiation_timed_out`
     (reading the live governance `timeout_config.negotiation_total_timeout_hours`) →
     `compensate_negotiation` → `_cancel_negotiation`. Seeds one ISSUED PO under a
     throwaway negotiation aged past the ceiling, drives that exact branch, and asserts
     the PO is undone and the negotiation ends CANCELLED / total_timeout_exceeded.

Deploy-independent: imports the modules the deployed node/recovery Lambdas import and
drives them in-process, so it validates the real DynamoDB schema + live governance
config today. It deliberately does NOT invoke the deployed `dev-buyer-team-recovery`
Lambda: that entrypoint is a GLOBAL sweep that resumes/cancels *every* non-terminal
negotiation (its own docstring: "invoked deliberately, not on a schedule"), which would
mutate real data. The recovery-owned units are tested here without that blast radius.

NOT billable — no agents, no Step Functions; a handful of DDB writes under synthetic,
self-cleaning ids. Opt-in via RUN_INTEGRATION=1; needs AWS creds, no VPC/NAT.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import pytest

os.environ.setdefault("ENV", "dev")
os.environ.setdefault("AWS_REGION", "us-east-1")

# Same import shape as test_saga_compensation_live: impl/orchestrator on sys.path →
# top-level `resilience`, exactly as the deployed executors import it.
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "orchestrator"))
from resilience import compensation as comp  # noqa: E402
from resilience import recovery as rec  # noqa: E402
from resilience.timeout_enforcement import is_negotiation_timed_out  # noqa: E402

from .conftest import REGION, TENANT  # noqa: E402

ENV = os.getenv("ENV", "dev")


def _t(suffix: str):
    return boto3.resource("dynamodb", region_name=REGION).Table(f"{ENV}-{suffix}")


# --------------------------------------------------------------------------- #
# 1. REQ-R502 concurrent-recovery lock                                        #
# --------------------------------------------------------------------------- #


def test_recovery_lock_grants_refuses_and_takes_over_on_expiry():
    neg = f"rec-lock-live-{uuid.uuid4()}"
    locks = _t("recovery-locks")
    pk = f"{TENANT}#{neg}"
    try:
        # First holder acquires.
        assert rec.acquire_recovery_lock(TENANT, neg, "instance-A") is True

        # A competing holder is refused while the lock is unexpired.
        assert rec.acquire_recovery_lock(TENANT, neg, "instance-B") is False

        # Age the lock past its expiry, then a new holder takes over (crashed-holder
        # takeover — the ConditionExpression keys on expires_at, not TTL deletion timing).
        past = int((datetime.now(timezone.utc) - timedelta(seconds=1)).timestamp())
        locks.update_item(
            Key={"pk": pk},
            UpdateExpression="SET expires_at = :p",
            ExpressionAttributeValues={":p": past},
        )
        assert rec.acquire_recovery_lock(TENANT, neg, "instance-B") is True

        # The row now records the new holder.
        row = locks.get_item(Key={"pk": pk})["Item"]
        assert row["instance_id"] == "instance-B"

        # Release removes the lock.
        rec.release_recovery_lock(TENANT, neg)
        assert "Item" not in locks.get_item(Key={"pk": pk})
    finally:
        locks.delete_item(Key={"pk": pk})


# --------------------------------------------------------------------------- #
# 2. §4.3 total-timeout → §4.4 compensate → CANCELLED                          #
# --------------------------------------------------------------------------- #


@pytest.fixture
def timed_out_negotiation():
    """Seed an AWARDED negotiation with one ISSUED PO, aged well past the total-timeout
    ceiling, plus the compensation the flow would have recorded. Clean up on teardown."""
    neg = f"rec-timeout-live-{uuid.uuid4()}"
    order_id = f"po-{uuid.uuid4()}"
    pk_neg = f"{TENANT}#{neg}"
    # 200h ago — comfortably past the 168h default ceiling.
    created_at = (datetime.now(timezone.utc) - timedelta(hours=200)).isoformat()

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
    _t("negotiations").put_item(
        Item={
            "tenant_id": TENANT,
            "negotiation_id": neg,
            "status": "AWARDED",
            "created_at": created_at,
        }
    )
    comp.record_compensation(TENANT, neg, "cancel_purchase_order", {"order_id": order_id})

    ids = {"neg": neg, "order_id": order_id, "created_at": created_at}
    try:
        yield ids
    finally:
        _t("orders").delete_item(Key={"pk": f"{TENANT}#{order_id}", "sk": "metadata"})
        _t("negotiations").delete_item(Key={"tenant_id": TENANT, "negotiation_id": neg})
        saga = comp._saga_table()
        for entry in comp._query_steps(pk_neg):
            saga.delete_item(Key={"pk": pk_neg, "sk": entry["sk"]})


def test_total_timeout_compensates_and_cancels(timed_out_negotiation):
    ids = timed_out_negotiation
    neg, order_id = ids["neg"], ids["order_id"]

    # §4.3 ceiling read from live governance: the aged negotiation is over, a fresh one isn't.
    assert is_negotiation_timed_out(ids["created_at"]) is True
    assert is_negotiation_timed_out(datetime.now(timezone.utc).isoformat()) is False

    # Drive the exact branch run_recovery_flow takes past the ceiling (§4.4 + terminal cancel).
    comp.compensate_negotiation(TENANT, neg, reason="total_timeout_exceeded")
    rec._cancel_negotiation(TENANT, neg)

    # The committed PO was undone...
    order = _t("orders").get_item(Key={"pk": f"{TENANT}#{order_id}", "sk": "metadata"})["Item"]
    assert order["status"] == "CANCELLED", order

    # ...and the negotiation ends terminal CANCELLED / total_timeout_exceeded.
    neg_row = _t("negotiations").get_item(Key={"tenant_id": TENANT, "negotiation_id": neg})["Item"]
    assert neg_row["status"] == "CANCELLED", neg_row
    assert neg_row["cancelled_reason"] == "total_timeout_exceeded", neg_row
