"""Live test: the deployed outbox poller drains a PENDING event idempotently.

Closes the "transactional outbox never exercised live" gap (PRD-006 §2.6). The
offline suite (`orchestrator/tests/test_resilience.py`) proves `drain_outbox`
against in-memory fakes; this drives the REAL deployed `dev-buyer-team-outbox-poller`
Lambda against the live `dev-outbox` table, the `dev-buyer-team-dlq` SQS queue, and
the Object-Lock (WORM) archive bucket — so the poller's IAM role, the conditional
PENDING→DISPATCHED transition, the SQS delivery, and the `dlq.archive_to_s3` tee are
all proven end-to-end.

Flow: enqueue one synthetic PENDING `dlq` event → invoke the poller → assert it
returns dispatched>=1, the row flipped PENDING→DISPATCHED, the failure payload
landed on the DLQ queue, and a WORM copy was archived to S3. Then invoke the poller
again and assert the row is NOT re-dispatched — the conditional transition makes
redelivery safe (at-least-once, no double-dispatch).

Uses a synthetic tenant id so no real tenant's partition or archive namespace is
touched. Teardown deletes the outbox row and drains the synthetic SQS message; the
S3 archive object is intentionally left (the bucket is WORM — leaving it is the
correct, tested behaviour), keyed under the synthetic tenant.

Doubly opt-in: needs RUN_INTEGRATION=1 *and* RUN_INTEGRATION_INVOKE=1 (invokes a
Lambda, writes SQS + S3). Needs the VPC NAT up. No agents, no SFN — not LLM-billable.
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_DNS, uuid4, uuid5

import boto3
import pytest

from .conftest import REGION

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_INVOKE") != "1",
    reason="set RUN_INTEGRATION_INVOKE=1 to invoke the live outbox poller (writes SQS/S3)",
)

ENV = os.getenv("ENV", "dev")
OUTBOX_TABLE = f"{ENV}-outbox"
POLLER = f"{ENV}-buyer-team-outbox-poller"
# Synthetic tenant — keeps the test off any real tenant's partition / archive namespace.
TEST_TENANT = str(uuid5(NAMESPACE_DNS, "buyer-team:outbox-live-test"))


@pytest.fixture(scope="module")
def poller_env():
    """Resolve the poller's real destination wiring (queue URL + archive bucket)."""
    lam = boto3.client("lambda", region_name=REGION)
    env = lam.get_function_configuration(FunctionName=POLLER)["Environment"]["Variables"]
    return {"dlq_url": env["BUYER_TEAM_DLQ_URL"], "archive_bucket": env["DLQ_ARCHIVE_BUCKET"]}


def _invoke_poller() -> dict:
    lam = boto3.client("lambda", region_name=REGION)
    resp = lam.invoke(FunctionName=POLLER, Payload=b"{}")
    return json.loads(resp["Payload"].read())


def _put_pending(table, *, event_id: str, destination: str, payload: dict) -> None:
    now = datetime.now(timezone.utc)
    table.put_item(
        Item={
            "pk": TEST_TENANT,
            "sk": event_id,
            "status": "PENDING",
            "destination": destination,
            "payload": json.dumps(payload, default=str),
            "created_at": now.isoformat(),
            "attempts": 0,
            "ttl": int((now + timedelta(days=14)).timestamp()),
        }
    )


def _drain_marker_from_queue(sqs, queue_url: str, marker: str, *, timeout: int = 30) -> bool:
    """Receive + delete our marker message from the queue; True if found. Other
    messages are received (briefly invisible) but not deleted, so they redeliver."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = sqs.receive_message(
            QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=2, VisibilityTimeout=5
        )
        for m in resp.get("Messages", []):
            if marker in m["Body"]:
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])
                return True
    return False


def test_outbox_poller_drains_dlq_event_idempotently(poller_env):
    ddb = boto3.resource("dynamodb", region_name=REGION)
    sqs = boto3.client("sqs", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)
    table = ddb.Table(OUTBOX_TABLE)

    marker = uuid4().hex
    neg_id = f"outbox-live-{marker}"
    event_id = f"outbox-live#{marker}"
    payload = {
        "negotiation_id": neg_id,
        "tenant_id": TEST_TENANT,
        "failed_node": "outbox_live_test",
        "error_type": "SyntheticError",
        "note": "synthetic outbox-poller live test — safe to ignore",
    }

    _put_pending(table, event_id=event_id, destination="dlq", payload=payload)
    try:
        # 1. The poller drains the PENDING row.
        res = _invoke_poller()
        assert res.get("dispatched", 0) >= 1, f"poller dispatched nothing: {res}"

        # 2. The row flipped PENDING -> DISPATCHED (conditional transition on the real table).
        row = None
        for _ in range(10):
            row = table.get_item(Key={"pk": TEST_TENANT, "sk": event_id}).get("Item")
            if row and row.get("status") == "DISPATCHED":
                break
            time.sleep(1)
        assert row and row.get("status") == "DISPATCHED", f"row not DISPATCHED: {row}"
        assert row.get("dispatched_at"), "no dispatched_at stamped"

        # 3. The failure payload was delivered to the DLQ SQS queue.
        assert _drain_marker_from_queue(sqs, poller_env["dlq_url"], marker), (
            "synthetic DLQ event never appeared on the queue"
        )

        # 4. A WORM copy was tee'd to the Object-Lock S3 archive (dlq.archive_to_s3).
        now = datetime.now(timezone.utc)
        prefix = f"{TEST_TENANT}/{now.year}/{now.month:02d}/{now.day:02d}/{neg_id}-"
        objs = s3.list_objects_v2(Bucket=poller_env["archive_bucket"], Prefix=prefix).get(
            "Contents", []
        )
        assert objs, f"no WORM archive object under s3://{poller_env['archive_bucket']}/{prefix}"

        # 5. Idempotent: a second sweep must NOT re-dispatch the already-DISPATCHED row.
        _invoke_poller()
        again = table.get_item(Key={"pk": TEST_TENANT, "sk": event_id})["Item"]
        assert again["status"] == "DISPATCHED"
        assert not _drain_marker_from_queue(sqs, poller_env["dlq_url"], marker, timeout=5), (
            "row was re-dispatched on the second sweep — at-least-once guard is broken"
        )
    finally:
        table.delete_item(Key={"pk": TEST_TENANT, "sk": event_id})
