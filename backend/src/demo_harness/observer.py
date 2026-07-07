"""Observer API — read-only projection + SSE + HITL approval proxy
(Seams S4/S5, PRD-020 §5.3).

Routes:
  GET  /demo/negotiations/{id}          — merged snapshot
  GET  /demo/negotiations/{id}/stream   — SSE timeline
  POST /demo/negotiations/{id}/approve  — HITL release via GraphClient
  GET  /demo/requisitions               — PR list (items, qty, status)
  POST /demo/requisitions               — create a PR (Seam S1)
  GET  /demo/suppliers                  — roster + communications
  GET  /demo/suppliers/{id}/rfqs        — per-negotiation RFQs/response/feedback for one supplier
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import boto3
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from test_tenant_app.clients.dynamo_client import dynamo_client
from test_tenant_app.clients.graph_client import graph_client
from test_tenant_app.clients.master_data_client import master_data_client

from demo_harness.config import TENANT_ID
from demo_harness.health import check_buyer_team
from demo_harness.offer_projection import (
    _DDB_THREAD_TIMEOUT,
    get_state,
    poll_once,
    set_correlation_id,
    subscribe,
    unsubscribe,
)

logger = logging.getLogger("demo_harness.observer")
router = APIRouter(prefix="/demo")


# ── Buyer Team reachability ───────────────────────────────────────


@router.get("/health")
def buyer_team_health():
    """Is the real Buyer Team orchestrator reachable? (Node 6 Lambda + master-store tables)"""
    return check_buyer_team()


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
        logger.info("negotiation %s not found (not yet started?)", negotiation_id)
        raise HTTPException(status_code=404, detail="Negotiation not found")

    # Read awards + orders from Dynamo (offloaded to thread to avoid blocking event loop)
    requisition_id = state.get("requisition_id")
    awards = []
    orders = []
    if requisition_id:
        try:
            awards = await asyncio.wait_for(
                asyncio.to_thread(dynamo_client.get_awards, TENANT_ID, requisition_id),
                timeout=_DDB_THREAD_TIMEOUT,
            )
        except (asyncio.TimeoutError, Exception):
            pass
        try:
            orders = await asyncio.wait_for(
                asyncio.to_thread(dynamo_client.get_orders, TENANT_ID),
                timeout=_DDB_THREAD_TIMEOUT,
            )
            orders = [o for o in orders if o.get("requisition_id") == requisition_id]
        except (asyncio.TimeoutError, Exception):
            pass

    return {
        **state,
        "awards": awards,
        "orders": orders,
    }


@router.get("/negotiations/{negotiation_id}/stream")
async def stream_negotiation(negotiation_id: str):
    """SSE timeline — push node progress, offers, status changes."""

    async def _read_awards_orders(requisition_id: str | None) -> tuple[list, list]:
        if not requisition_id:
            return [], []
        awards, orders = [], []
        try:
            awards = await asyncio.wait_for(
                asyncio.to_thread(dynamo_client.get_awards, TENANT_ID, requisition_id),
                timeout=_DDB_THREAD_TIMEOUT,
            )
        except (asyncio.TimeoutError, Exception):
            pass
        try:
            orders = await asyncio.wait_for(
                asyncio.to_thread(dynamo_client.get_orders, TENANT_ID),
                timeout=_DDB_THREAD_TIMEOUT,
            )
            orders = [o for o in orders if o.get("requisition_id") == requisition_id]
        except (asyncio.TimeoutError, Exception):
            pass
        return awards, orders

    async def event_generator():
        q = subscribe(negotiation_id)
        try:
            # Send initial snapshot (with awards + orders, same as GET endpoint)
            state = await poll_once(negotiation_id)
            if state:
                awards, orders = await _read_awards_orders(state.get("requisition_id"))
                yield {
                    "event": "snapshot",
                    "data": json.dumps({**state, "awards": awards, "orders": orders}, default=str),
                }
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
    logger.info("approval decision=%s requisition_id=%s", decision, requisition_id)

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

        # The gate may have already resolved (race: orchestrator moved past
        # PENDING_APPROVAL before our Lambda invocation reached it). In that
        # case verify the negotiation state — if it's moved past PENDING_APPROVAL,
        # treat as success rather than surfacing an error to the user.
        if (
            isinstance(result, dict)
            and result.get("status") == "ERROR"
            and result.get("reason") == "no_pending_approval"
        ):
            try:
                state = poll_once(result.get("negotiation_id", ""))
                if state and state.get("status") != "PENDING_APPROVAL":
                    logger.info(
                        "gate already resolved for %s — treating as approved", requisition_id
                    )
                    return {
                        "status": "ok",
                        "decision": decision,
                        "result": {"status": "ALREADY_RESOLVED", "state": state},
                    }
            except Exception:
                pass

        logger.info("approval %s result for %s: %s", decision, requisition_id, result)
        return {"status": "ok", "decision": decision, "result": result}
    except Exception as e:
        logger.exception("approval %s failed for %s", decision, requisition_id)
        raise HTTPException(status_code=500, detail=str(e))


# ── Trace resolution ──────────────────────────────────────────────


@router.get("/negotiations/{negotiation_id}/traces")
async def get_trace_urls(negotiation_id: str):
    """Resolve SFN execution + X-Ray trace URLs for a negotiation."""
    from datetime import timedelta

    urls: dict = {"sfn": None, "xray": None}

    # SFN: execution name is deterministic (neg-{negotiation_id})
    try:
        sfn = boto3.client("stepfunctions", region_name=os.getenv("AWS_REGION", "us-east-1"))
        machines = sfn.list_state_machines()["stateMachines"]
        arn = next((m["stateMachineArn"] for m in machines if "buyer-team" in m["name"]), None)
        if arn:
            name = f"neg-{negotiation_id}"
            exec_arn = f"{arn.replace('stateMachine', 'execution')}:{name}"
            urls["sfn"] = (
                f"https://{os.getenv('AWS_REGION', 'us-east-1')}.console.aws.amazon.com"
                f"/states/home?region={os.getenv('AWS_REGION', 'us-east-1')}"
                f"#/executions/details/{exec_arn.replace(':', '%3A').replace('/', '%2F')}"
            )
            # Get trace ID from execution history (TaskStateExited events carry trace headers)
            try:
                history = sfn.get_execution_history(
                    executionArn=exec_arn, maxResults=5, reverseOrder=True
                )
                exec_start = None
                exec_end = None
                for evt in history["events"]:
                    if evt["type"] == "ExecutionSucceeded":
                        exec_end = evt["timestamp"]
                    elif evt["type"] == "ExecutionStarted":
                        exec_start = evt["timestamp"]
                if exec_start and exec_end:
                    xray = boto3.client("xray", region_name=os.getenv("AWS_REGION", "us-east-1"))
                    # X-Ray indexing has sub-minute latency but the query window must be
                    # wide enough. Use ±5 minutes around the execution.
                    traces = xray.get_trace_summaries(
                        StartTime=exec_start - timedelta(minutes=5),
                        EndTime=exec_end + timedelta(minutes=5),
                        TimeRangeType="TraceId",
                    )["TraceSummaries"]
                    duration = (exec_end - exec_start).total_seconds()
                    # Find trace closest in duration and start time
                    best = None
                    best_score = float("inf")
                    for t in traces:
                        dur_diff = abs(t["Duration"] - duration)
                        time_diff = abs((t["StartTime"] - exec_start).total_seconds())
                        score = dur_diff + time_diff * 0.5
                        if score < best_score and dur_diff < 20:
                            best_score = score
                            best = t
                    if best:
                        urls["xray"] = (
                            f"https://{os.getenv('AWS_REGION', 'us-east-1')}.console.aws.amazon.com"
                            f"/xray/home?region={os.getenv('AWS_REGION', 'us-east-1')}"
                            f"#/traces/{best['Id']}"
                        )
            except Exception:
                pass
    except Exception:
        logger.exception("trace resolution failed for %s", negotiation_id)

    return urls


# ── PR Generator (Seam S1) ────────────────────────────────────────


@router.get("/items")
def preview_item(quadrant: str):
    """Preview what item will be bought for a given Kraljic quadrant."""
    from demo_harness.seed import ITEMS

    item_def = ITEMS.get(quadrant.upper())
    if not item_def:
        raise HTTPException(status_code=404, detail=f"Unknown quadrant: {quadrant}")
    return {
        "sku": item_def["sku"],
        "name": item_def["name"],
        "ata": item_def["ata"],
        "estimated_unit_price": item_def["estimated_unit_price"],
        "lead_time_days": item_def["lead_time_days"],
    }


@router.get("/requisitions")
def list_requisitions():
    """Blue Jets PRs with their line items (name/sku/quantity) — click-through list."""
    prs = master_data_client.list_prs(TENANT_ID)
    logger.info("listed %d requisitions for tenant %s", len(prs), TENANT_ID)
    return prs


@router.post("/requisitions")
def create_requisition(body: PRRequest):
    """Generate + submit a PR via the canonical master-store intake."""
    from demo_harness.pr_generator import build_pr

    logger.info("creating PR quadrant=%s quantity=%d", body.quadrant, body.quantity)
    try:
        pr = build_pr(body.quadrant, body.quantity)
    except ValueError as e:
        logger.warning("PR creation rejected: %s", e)
        raise HTTPException(status_code=400, detail=str(e))

    logger.info(
        "PR created requisition_id=%s negotiation_id=%s correlation_id=%s",
        pr["requisition_id"],
        pr["negotiation_id"],
        pr.get("correlation_id", ""),
    )
    if pr.get("correlation_id"):
        set_correlation_id(pr["negotiation_id"], pr["correlation_id"])
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
        logger.info("suppliers: %d rows for tenant %s", len(items), TENANT_ID)
        return to_native(items)
    except Exception:
        logger.exception("supplier query failed")
        return []


# `{env}-communications` has no supplier_id index (pk/sk only, keyed by negotiation) —
# scanning filtered by supplier_id is the only query path, same tradeoff `get_orders`
# already makes on `test-tenant-orders`. supplier_id is a uuid5 hash of tenant+name, so
# no cross-tenant filter is needed to keep this scoped to one supplier.
INVITATION_TYPES = {"BID_INVITATION", "AUCTION_INVITATION"}
FEEDBACK_TYPES = {"AWARD_NOTIFICATION", "REJECTION_NOTIFICATION", "AUCTION_ROUND_FEEDBACK"}


@router.get("/suppliers/{supplier_id}/rfqs")
def get_supplier_rfqs(supplier_id: str):
    """For one supplier: every negotiation it was invited to, its response (bid),
    and the feedback it received back (award/rejection/auction-round feedback)."""
    from boto3.dynamodb.conditions import Attr, Key
    from test_tenant_app.clients.ddb import table, to_native

    comms = to_native(
        table("communications")
        .scan(FilterExpression=Attr("supplier_id").eq(supplier_id))
        .get("Items", [])
    )
    bids = to_native(
        table("bids").query(KeyConditionExpression=Key("tenant_id").eq(TENANT_ID)).get("Items", [])
    )
    bids_by_negotiation = {
        b["negotiation_id"]: b for b in bids if b.get("supplier_id") == supplier_id
    }

    by_negotiation: dict[str, dict] = {}
    for c in comms:
        negotiation_id = c.get("negotiation_id")
        if not negotiation_id:
            continue
        entry = by_negotiation.setdefault(
            negotiation_id, {"negotiation_id": negotiation_id, "invitations": [], "feedback": []}
        )
        if c["type"] in INVITATION_TYPES:
            entry["invitations"].append(c)
        elif c["type"] in FEEDBACK_TYPES:
            entry["feedback"].append(c)

    # A bid can exist with no invitation on record (e.g. the resilience fallback path
    # prices a bid without the agent ever calling send_bid_invitation) — surface it anyway.
    for negotiation_id in bids_by_negotiation:
        by_negotiation.setdefault(
            negotiation_id, {"negotiation_id": negotiation_id, "invitations": [], "feedback": []}
        )

    for negotiation_id, entry in by_negotiation.items():
        entry["response"] = bids_by_negotiation.get(negotiation_id)
        neg = dynamo_client.get_negotiation(TENANT_ID, negotiation_id)
        entry["status"] = neg.get("status") if neg else None
        entry["quadrant"] = neg.get("kraljic_quadrant") if neg else None

    def _latest_ts(entry: dict) -> float:
        """Latest timestamp across invitations, feedback, and bid response."""
        ts = 0.0
        for c in entry.get("invitations", []):
            v = c.get("created_at")
            ts = max(ts, float(v) if isinstance(v, (int, float)) else 0)
        for c in entry.get("feedback", []):
            v = c.get("created_at")
            ts = max(ts, float(v) if isinstance(v, (int, float)) else 0)
        bid = entry.get("response")
        if bid:
            v = bid.get("created_at") or bid.get("priced_at")
            ts = max(ts, float(v) if isinstance(v, (int, float)) else 0)
        return ts

    result = sorted(by_negotiation.values(), key=_latest_ts, reverse=True)
    logger.info("supplier %s: %d RFQ negotiations", supplier_id, len(result))
    return result
