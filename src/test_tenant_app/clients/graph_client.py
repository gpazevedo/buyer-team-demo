"""GraphClient — triggers the Graph Orchestrator (PRD-002).

SKILL_MODE=stub  simulates workflow progression.
SKILL_MODE=live  starts the deployed Step Functions PR→PO DAG directly
                 (the documented direct shortcut — see IMPLEMENTATION_PLAN.md WS-C).
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
STATE_MACHINE_ARN = os.getenv("STATE_MACHINE_ARN", "")
REGION = os.getenv("AWS_REGION", "us-east-1")


@cache
def _sfn():
    return boto3.client("stepfunctions", region_name=REGION)


def _execution_name(tenant_id: str, requisition_id: str) -> str:
    """Deterministic execution name so a duplicate trigger for the same PR is a no-op
    (SFN rejects a re-used name with ExecutionAlreadyExists). Mirrors the orchestrator's
    `mcp_servers/step_functions_orchestrator/server.py` naming."""
    return "neg-" + str(uuid5(NAMESPACE_DNS, f"{tenant_id}:negotiation:{requisition_id}"))


class GraphClient:
    def ingest_pr(self, tenant_id: str, requisition_id: str) -> dict:
        """Trigger PR ingestion through the graph orchestrator.

        Live mode stands in for the canonical `ingest_pr` skill: it promotes the
        freshly-created PR from NEW to VALIDATED (the orchestrator's Node 1 entry
        guard consumes a VALIDATED PR, never a NEW one — see
        `orchestrator/node_ingest_validate.py`), then starts the deployed Step
        Functions execution. The execution name is deterministic so re-firing the
        same PR is idempotent.
        """
        if SKILL_MODE == "stub":
            return {"status": "accepted", "requisition_id": requisition_id}
        if not STATE_MACHINE_ARN:
            raise RuntimeError("STATE_MACHINE_ARN not configured")
        self._mark_validated(tenant_id, requisition_id)
        name = _execution_name(tenant_id, requisition_id)
        payload = json.dumps({"tenant_id": tenant_id, "requisition_id": requisition_id})
        try:
            resp = _sfn().start_execution(
                stateMachineArn=STATE_MACHINE_ARN, name=name, input=payload
            )
            logger.info("started negotiation %s for PR %s", name, requisition_id)
            return {"status": "started", "execution_arn": resp["executionArn"],
                    "requisition_id": requisition_id}
        except _sfn().exceptions.ExecutionAlreadyExists:
            arn = STATE_MACHINE_ARN.replace(":stateMachine:", ":execution:") + f":{name}"
            return {"status": "already_started", "execution_arn": arn,
                    "requisition_id": requisition_id}

    def _mark_validated(self, tenant_id: str, requisition_id: str) -> None:
        """Promote the PR NEW→VALIDATED so Node 1 admits it (mirrors the canonical
        ingest_pr skill's domain upsert at status VALIDATED)."""
        from datetime import datetime, timezone

        from test_tenant_app.clients.ddb import table

        table("requisitions").update_item(
            Key={"pk": f"{tenant_id}#{requisition_id}", "sk": "metadata"},
            UpdateExpression="SET #s = :v, updated_at = :u",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":v": "VALIDATED",
                ":u": datetime.now(tz=timezone.utc).isoformat(),
            },
        )

    def approve_award(self, tenant_id: str, requisition_id: str) -> dict:
        if SKILL_MODE == "stub":
            return {"status": "approved"}
        return {"status": "approved", "workflow": "not_started"}

    def reject_award(self, tenant_id: str, requisition_id: str) -> dict:
        if SKILL_MODE == "stub":
            return {"status": "rejected"}
        return {"status": "rejected", "workflow": "not_started"}


graph_client = GraphClient()
