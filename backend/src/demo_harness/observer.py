"""Observer API — read-only projection + SSE + HITL approval proxy
(Seams S4/S5, PRD-020 §5.3).

Routes:
  GET  /demo/negotiations/{id}          — merged snapshot
  GET  /demo/negotiations/{id}/stream   — SSE timeline
  POST /demo/negotiations/{id}/approve  — HITL release via GraphClient
  GET  /demo/suppliers                  — roster + communications
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from test_tenant_app.clients.dynamo_client import dynamo_client
from test_tenant_app.clients.graph_client import graph_client

from demo_harness.config import TENANT_ID
from demo_harness.offer_projection import get_state, poll_once, subscribe, unsubscribe

logger = logging.getLogger("demo_harness.observer")
router = APIRouter(prefix="/demo")


# ── Request/response models ───────────────────────────────────────


class ApproveRequest(BaseModel):
    decision: str  # APPROVED | REJECTED | CYCLE_BACK
    reason: str | None = None


class PRRequest(BaseModel):
    quadrant: str = "NON_CRITICAL"
    quantity: int = 1


# ── Negotiation endpoints ─────────────────────────────────────────


@router.get("/negotiations/{negotiation_id}")
async def get_negotiation(negotiation_id: str):
    """Merged snapshot: SFN state + entities + communications + bids."""
    # Poll to refresh state
    state = await poll_once(negotiation_id)
    if not state:
        # Try a fresh poll even if not tracked yet
        state = get_state(negotiation_id)
    if not state:
        raise HTTPException(status_code=404, detail="Negotiation not found")

    # Read awards + orders from Dynamo
    requisition_id = state.get("requisition_id")
    awards = []
    orders = []
    if requisition_id:
        try:
            awards = dynamo_client.get_awards(TENANT_ID, requisition_id)
        except Exception:
            pass
        try:
            orders = dynamo_client.get_orders(TENANT_ID)
            orders = [o for o in orders if o.get("requisition_id") == requisition_id]
        except Exception:
            pass

    return {
        **state,
        "awards": awards,
        "orders": orders,
    }


@router.get("/negotiations/{negotiation_id}/stream")
async def stream_negotiation(negotiation_id: str):
    """SSE timeline — push node progress, offers, status changes."""

    async def event_generator():
        q = subscribe(negotiation_id)
        try:
            # Send initial snapshot
            state = await poll_once(negotiation_id)
            if state:
                yield {"event": "snapshot", "data": json.dumps(state, default=str)}
            else:
                yield {"event": "waiting", "data": json.dumps({"negotiation_id": negotiation_id})}

            while True:
                try:
                    data = await asyncio.wait_for(q.get(), timeout=30)
                    yield {"event": "update", "data": data}
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": "{}"}
        except asyncio.CancelledError:
            pass
        finally:
            unsubscribe(negotiation_id, q)

    return EventSourceResponse(event_generator())


# ── HITL approval proxy (Seam S4) ─────────────────────────────────


@router.post("/negotiations/{requisition_id}/approve")
def approve_negotiation(requisition_id: str, body: ApproveRequest):
    """Release a paused Approval Gate via GraphClient (direct Lambda invoke).

    decision ∈ {APPROVED, REJECTED, CYCLE_BACK}.
    Mirrors test_tenant_app /api/requisitions/{id}/approve|reject|cycle_back.
    """
    decision = body.decision.upper()
    if decision not in ("APPROVED", "REJECTED", "CYCLE_BACK"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid decision: {body.decision}. Must be APPROVED, REJECTED, or CYCLE_BACK",
        )

    approver = {"user_id": "demo-harness", "tenant_id": TENANT_ID, "claims": {}}

    try:
        if decision == "APPROVED":
            result = graph_client.approve_award(TENANT_ID, requisition_id, approver)
        elif decision == "REJECTED":
            result = graph_client.reject_award(
                TENANT_ID,
                requisition_id,
                reason=body.reason or "Demo-approver rejected",
                approver=approver,
            )
        else:
            result = graph_client.cycle_back_award(TENANT_ID, requisition_id, approver)
        return {"status": "ok", "decision": decision, "result": result}
    except Exception as e:
        logger.exception("approval failed for %s", requisition_id)
        raise HTTPException(status_code=500, detail=str(e))


# ── PR Generator (Seam S1) ────────────────────────────────────────


@router.post("/requisitions")
def create_requisition(body: PRRequest):
    """Generate + submit a PR via the canonical master-store intake."""
    from demo_harness.pr_generator import build_pr

    try:
        pr = build_pr(body.quadrant, body.quantity)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return pr


# ── Supplier roster ───────────────────────────────────────────────


@router.get("/suppliers")
def list_suppliers():
    """Roster of Blue Jets suppliers + per-negotiation communications."""
    from boto3.dynamodb.conditions import Key
    from test_tenant_app.clients.ddb import table, to_native

    try:
        items = (
            table("suppliers")
            .query(KeyConditionExpression=Key("tenant_id").eq(TENANT_ID))
            .get("Items", [])
        )
        return to_native(items)
    except Exception:
        return []
