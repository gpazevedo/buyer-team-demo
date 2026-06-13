"""GraphClient — calls the Graph Orchestrator (PRD-002).

SKILL_MODE=stub  simulates workflow progression.
SKILL_MODE=live  calls the real AgentCore Gateway (not implemented yet).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("graph_client")
SKILL_MODE = os.getenv("SKILL_MODE", "stub")


class GraphClient:
    def ingest_pr(self, tenant_id: str, requisition_id: str) -> dict:
        """Trigger PR ingestion through the graph orchestrator.

        Live mode is an honest no-op: the Graph Orchestrator (PRD-002) is not
        deployed yet, so the PR is persisted but no negotiation workflow starts.
        """
        if SKILL_MODE == "stub":
            return {"status": "accepted", "requisition_id": requisition_id}
        logger.warning(
            "graph orchestrator not deployed; PR %s persisted without workflow",
            requisition_id,
        )
        return {"status": "accepted", "requisition_id": requisition_id, "workflow": "not_started"}

    def approve_award(self, tenant_id: str, requisition_id: str) -> dict:
        if SKILL_MODE == "stub":
            return {"status": "approved"}
        return {"status": "approved", "workflow": "not_started"}

    def reject_award(self, tenant_id: str, requisition_id: str) -> dict:
        if SKILL_MODE == "stub":
            return {"status": "rejected"}
        return {"status": "rejected", "workflow": "not_started"}


graph_client = GraphClient()
