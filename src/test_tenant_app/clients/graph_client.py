"""GraphClient — releases the orchestrator's paused Approval Gate (PRD-002 Node 6).

The PR *trigger* is no longer here: in the canonical design the app writes the PR to
the master store and the DynamoDB Stream → pr_event_router → ingest_pr →
start_negotiation_workflow chain starts the workflow (see master_data_client.create_pr).
What remains is the HITL approval callback — a legitimate app→orchestrator call that
returns the task token Node 6 paused on so Node 7 can issue the PO.

SKILL_MODE=stub  no-op acknowledgements.
SKILL_MODE=live  invokes Node 6's resume_approval Lambda (APPROVED / REJECTED).
"""

from __future__ import annotations

import json
import logging
import os
from functools import cache
from uuid import NAMESPACE_DNS, uuid5

import boto3

logger = logging.getLogger("graph_client")
SKILL_MODE = os.getenv("SKILL_MODE", "stub")
REGION = os.getenv("AWS_REGION", "us-east-1")
ENV = os.getenv("ENV", "dev")
APPROVAL_GATE_FUNCTION = os.getenv(
    "APPROVAL_GATE_FUNCTION", f"{ENV}-buyer-team-node6-approval-gate"
)


@cache
def _lambda():
    return boto3.client("lambda", region_name=REGION)


def _negotiation_id(tenant_id: str, requisition_id: str) -> str:
    """The one negotiation per PR — deterministic, matching Node 1's
    `_deterministic_negotiation_id` (the execution name minus the `neg-` prefix)."""
    return str(uuid5(NAMESPACE_DNS, f"{tenant_id}:negotiation:{requisition_id}"))


class GraphClient:
    def approve_award(self, tenant_id: str, requisition_id: str) -> dict:
        """Release a paused Approval Gate with an APPROVED decision so Node 7 runs.

        Node 6 paused on `waitForTaskToken`; the approval only completes the
        workflow if that token is returned. Live mode invokes Node 6's
        `resume_approval` (via its Lambda) which sends task success → Node 7 →
        PO ISSUED.
        """
        if SKILL_MODE == "stub":
            return {"status": "approved"}
        return self._resume_approval(tenant_id, requisition_id, "APPROVED")

    def reject_award(self, tenant_id: str, requisition_id: str, reason: str | None = None) -> dict:
        """Release a paused Approval Gate with a REJECTED decision (cancels the
        negotiation + requisition). A no-op if nothing is paused."""
        if SKILL_MODE == "stub":
            return {"status": "rejected"}
        return self._resume_approval(tenant_id, requisition_id, "REJECTED", reason)

    def _resume_approval(
        self, tenant_id: str, requisition_id: str, decision: str, reason: str | None = None
    ) -> dict:
        negotiation_id = _negotiation_id(tenant_id, requisition_id)
        payload = {
            "decision": decision,
            "tenant_id": tenant_id,
            "negotiation_id": negotiation_id,
            "approver": {"user_id": "test-tenant-app"},
        }
        if reason:
            payload["reason"] = reason
        resp = _lambda().invoke(
            FunctionName=APPROVAL_GATE_FUNCTION,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload).encode(),
        )
        result = json.loads(resp["Payload"].read() or b"{}")
        logger.info(
            "resume_approval %s for PR %s -> %s", decision, requisition_id, result.get("status")
        )
        return result


graph_client = GraphClient()
