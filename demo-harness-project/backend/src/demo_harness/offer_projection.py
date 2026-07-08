"""Supplier-Offer Projection — polls {env}-communications + {env}-bids and
joins them into a per-supplier offer view (Seams S2/S3, PRD-020 §5.2).

Design 1 only (observed offers). Read-only — never writes to {env}-bids.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import overload
from uuid import uuid4

from boto3.dynamodb.conditions import Key
from test_tenant_app.clients.ddb import table as _live_table
from test_tenant_app.clients.ddb import to_native
from test_tenant_app.clients.dynamo_client import _supplier_name_map, dynamo_client

from demo_harness.config import (
    OBSERVER_POLL_SECONDS,
    TENANT_ID,
)

# DynamoDB calls run in threads to avoid blocking the event loop. A hung
# thread permanently occupies the default thread pool, which starves sync
# endpoints (uvicorn runs those on the same pool). Cap each call at 5 s.
_DDB_THREAD_TIMEOUT = 5.0

# Supplier names rarely change; avoid refetching the map every ~1s poll cycle.
_SUPPLIER_NAME_CACHE_TTL = 30.0
_supplier_name_cache: dict[str, tuple[float, dict]] = {}


def _cached_supplier_name_map(tenant_id: str) -> dict:
    now = time.monotonic()
    cached = _supplier_name_cache.get(tenant_id)
    if cached and now - cached[0] < _SUPPLIER_NAME_CACHE_TTL:
        return cached[1]
    names = _supplier_name_map(tenant_id)
    _supplier_name_cache[tenant_id] = (now, names)
    return names


logger = logging.getLogger("demo_harness.offer_projection")

# In-memory projection: negotiation_id -> {suppliers, offers, invitations, status}
_state: dict[str, dict] = {}

# SSE subscribers: {negotiation_id: [asyncio.Queue]}
_subscribers: dict[str, list[asyncio.Queue]] = {}

# Track published event IDs to prevent duplicates from concurrent poll_once callers.
_published_statuses: dict[str, str] = {}
_published_awards: set[str] = set()
_published_orders: set[str] = set()
_published_offers: set[str] = set()
_published_invites: set[str] = set()
_published_feedback: set[str] = set()
_published_classifications: set[str] = set()
_synthetic_order_ids: set[str] = set()  # tracked in-memory since DynamoDB drops origin field
_synthetic_neg_ids: set[str] = set()  # prevent duplicate synthetic orders per negotiation
_correlation_ids: dict[str, str] = {}  # negotiation_id → correlation_id for end-to-end tracing
_global_subscribers: list[asyncio.Queue] = []  # for all-negotiation listeners


@overload
def get_state(negotiation_id: str) -> dict | None: ...
@overload
def get_state(negotiation_id: None = None) -> list[dict]: ...
def get_state(negotiation_id: str | None = None) -> dict | list[dict] | None:
    """Return current projection state for one or all negotiations."""
    if negotiation_id:
        return _state.get(negotiation_id)
    return list(_state.values())


def find_by_requisition_id(requisition_id: str) -> dict | None:
    """Find a negotiation state by its requisition ID."""
    for s in _state.values():
        if s.get("requisition_id") == requisition_id:
            return s
    return None


def set_correlation_id(negotiation_id: str, correlation_id: str) -> None:
    """Register a correlation ID for a negotiation (called at PR creation time)."""
    _correlation_ids[negotiation_id] = correlation_id


def subscribe(negotiation_id: str | None = None) -> asyncio.Queue:
    """Register an SSE subscriber queue. Returns queue that receives events."""
    q: asyncio.Queue = asyncio.Queue()
    if negotiation_id:
        _subscribers.setdefault(negotiation_id, []).append(q)
    else:
        _global_subscribers.append(q)
    return q


def unsubscribe(negotiation_id: str | None, q: asyncio.Queue) -> None:
    """Remove an SSE subscriber queue."""
    if negotiation_id and negotiation_id in _subscribers:
        _subscribers[negotiation_id] = [s for s in _subscribers[negotiation_id] if s is not q]
    else:
        _global_subscribers[:] = [s for s in _global_subscribers if s is not q]


async def _publish(negotiation_id: str, event: dict) -> None:
    """Push an event to all subscribers for this negotiation + global listeners."""
    data = json.dumps(event, default=str)
    targets = _subscribers.get(negotiation_id, []) + _global_subscribers
    for q in targets:
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            pass


_FEEDBACK_TYPES = {"AUCTION_ROUND_FEEDBACK"}


def _query_communications(negotiation_id: str) -> list[dict]:
    """Read communications for one negotiation from DynamoDB."""
    entries = dynamo_client.get_communications(TENANT_ID, negotiation_id)
    return [e for e in entries if e.get("type") == "BID_INVITATION"]


def _query_feedback(negotiation_id: str) -> list[dict]:
    """Read auction-round feedback communications for one negotiation."""
    entries = dynamo_client.get_communications(TENANT_ID, negotiation_id)
    return [e for e in entries if e.get("type") in _FEEDBACK_TYPES]


def _query_bids(negotiation_id: str) -> list[dict]:
    """Read ALL bids for one negotiation from {env}-bids (scoped to tenant)."""
    table = _live_table("bids")
    resp = table.query(KeyConditionExpression=Key("tenant_id").eq(TENANT_ID))
    rows = to_native(resp.get("Items", []))
    return [b for b in rows if b.get("negotiation_id") == negotiation_id]


def _query_negotiation(negotiation_id: str) -> dict | None:
    """Read negotiation row for status/quadrant/strategy."""
    neg = dynamo_client.get_negotiation(TENANT_ID, negotiation_id)
    return neg


def _fetch_dynamo_data(negotiation_id: str) -> tuple:
    """Synchronous DynamoDB reads — run in thread to avoid blocking event loop."""
    neg = _query_negotiation(negotiation_id)
    bids = _query_bids(negotiation_id)
    invitations = _query_communications(negotiation_id)
    feedback = _query_feedback(negotiation_id)
    names = _cached_supplier_name_map(TENANT_ID)
    return neg, bids, invitations, feedback, names


async def poll_once(negotiation_id: str) -> dict | None:
    """Poll all data sources for one negotiation; update projection state.
    Returns the updated state dict, or None if nothing changed.
    """
    try:
        neg, bids, invitations, feedback, names = await asyncio.wait_for(
            asyncio.to_thread(_fetch_dynamo_data, negotiation_id),
            timeout=_DDB_THREAD_TIMEOUT,
        )
    except (asyncio.TimeoutError, Exception):
        logger.exception("poll failed for %s", negotiation_id)
        return None

    if not neg and negotiation_id not in _state:
        return None

    prev = _state.get(negotiation_id, {})
    prev_bids = prev.get("bids", [])

    # Fetch awards + orders early so they can be included in the state saved
    # before event publishing — prevents concurrent poll_once callers from
    # seeing incomplete state (missing award_ids/order_ids).
    requisition_id = neg.get("requisition_id") if neg else None
    if requisition_id:
        try:
            current_awards = await asyncio.wait_for(
                asyncio.to_thread(dynamo_client.get_awards, TENANT_ID, requisition_id),
                timeout=_DDB_THREAD_TIMEOUT,
            )
        except (asyncio.TimeoutError, Exception):
            current_awards = []
        try:
            current_orders_all = await asyncio.wait_for(
                asyncio.to_thread(dynamo_client.get_orders, TENANT_ID),
                timeout=_DDB_THREAD_TIMEOUT,
            )
            current_orders_list = [
                o for o in current_orders_all if o.get("requisition_id") == requisition_id
            ]
        except (asyncio.TimeoutError, Exception):
            current_orders_list = []
        # If real (orchestrator) orders exist, delete any synthetic duplicates
        real_orders = [o for o in current_orders_list if o["order_id"] not in _synthetic_order_ids]
        synthetic_orders = [o for o in current_orders_list if o["order_id"] in _synthetic_order_ids]
        if real_orders and synthetic_orders:
            for syn in synthetic_orders:
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(
                            _live_table("test-tenant-orders").delete_item,
                            Key={"pk": f"{TENANT_ID}#{syn['order_id']}", "sk": "metadata"},
                        ),
                        timeout=_DDB_THREAD_TIMEOUT,
                    )
                    _synthetic_order_ids.discard(syn["order_id"])
                    logger.info("removed stale synthetic order %s", syn["order_id"])
                except (asyncio.TimeoutError, Exception):
                    pass
            current_orders_list = real_orders
    else:
        current_awards = []
        current_orders_list = []
    current_award_ids = {a["award_id"] for a in current_awards if a.get("award_id")}
    current_order_ids = {o["order_id"] for o in current_orders_list if o.get("order_id")}

    # Detect new RFQ invitations
    prev_invite_ids = {
        i["communication_id"] for i in prev.get("invitations", []) if i.get("communication_id")
    }
    current_invite_ids = {i["communication_id"] for i in invitations if i.get("communication_id")}
    new_invite_ids = current_invite_ids - prev_invite_ids
    if new_invite_ids:
        for invite in invitations:
            if invite["communication_id"] in new_invite_ids:
                cid = invite["communication_id"]
                if cid in _published_invites:
                    continue
                _published_invites.add(cid)
                logger.info(
                    "rfq sent negotiation=%s supplier=%s",
                    negotiation_id,
                    invite.get("supplier_name") or invite.get("supplier_id"),
                )
                await _publish(
                    negotiation_id,
                    {
                        "event": "rfq_sent",
                        "negotiation_id": negotiation_id,
                        "communication_id": cid,
                        "supplier_id": invite.get("supplier_id"),
                        "supplier_name": invite.get("supplier_name"),
                        "created_at": invite.get("created_at"),
                    },
                )

    # Detect new auction-round feedback
    prev_feedback_ids = {
        f["communication_id"] for f in prev.get("feedback", []) if f.get("communication_id")
    }
    current_feedback_ids = {f["communication_id"] for f in feedback if f.get("communication_id")}
    new_feedback_ids = current_feedback_ids - prev_feedback_ids
    if new_feedback_ids:
        for f in feedback:
            if (
                f["communication_id"] in new_feedback_ids
                and f["communication_id"] not in _published_feedback
            ):
                _published_feedback.add(f["communication_id"])
                logger.info(
                    "auction round feedback negotiation=%s supplier=%s rank=%s",
                    negotiation_id,
                    f.get("supplier_name") or f.get("supplier_id"),
                    f.get("current_rank"),
                )
                await _publish(negotiation_id, {**f, "event": "auction_round_feedback"})

    # Detect new or changed bids (amount or evaluation_rank changed)
    priced_bids = [b for b in bids if b.get("total_amount") or b.get("amount")]
    prev_priced = {
        (b["bid_id"], b.get("amount") or b.get("total_amount"), b.get("evaluation_rank"))
        for b in prev_bids
        if b.get("total_amount") or b.get("amount")
    }
    current_priced = {
        (b["bid_id"], b.get("amount") or b.get("total_amount"), b.get("evaluation_rank"))
        for b in priced_bids
    }
    new_priced_ids = {fp[0] for fp in current_priced - prev_priced}

    for b in bids:
        b["supplier_name"] = (
            b.get("supplier_name") or names.get(b.get("supplier_id")) or b.get("supplier_id")
        )

    # For strategies that bypass the agent (auto-priced SPOT_BID/COMPETITIVE_AUCTION),
    # no BID_INVITATION communications exist — derive supplier info from bids instead
    # and publish the rfq_sent event so the frontend shows the supplier invitation step.
    if not invitations and bids:
        seen = set()
        for b in bids:
            sid = b.get("supplier_id")
            if sid and sid not in seen:
                seen.add(sid)
                invite = {
                    "communication_id": f"auto-{sid}",
                    "type": "BID_INVITATION",
                    "supplier_id": sid,
                    "supplier_name": b.get("supplier_name") or names.get(sid, sid),
                    "created_at": b.get("created_at") or b.get("priced_at"),
                }
                invitations.append(invite)
                # Also fire rfq_sent for auto-derived invitations (missed by the
                # DynamoDB-based detection above since no BID_INVITATION record exists).
                if invite["communication_id"] not in prev_invite_ids:
                    logger.info(
                        "rfq sent (auto-derived) negotiation=%s supplier=%s",
                        negotiation_id,
                        invite.get("supplier_name") or invite.get("supplier_id"),
                    )
                    await _publish(
                        negotiation_id,
                        {
                            "event": "rfq_sent",
                            "negotiation_id": negotiation_id,
                            "communication_id": invite["communication_id"],
                            "supplier_id": sid,
                            "supplier_name": invite["supplier_name"],
                            "created_at": invite["created_at"],
                        },
                    )

    state = {
        "negotiation_id": negotiation_id,
        "requisition_id": neg.get("requisition_id") if neg else None,
        "status": neg.get("status") if neg else None,
        "quadrant": neg.get("kraljic_quadrant") if neg else None,
        "strategy": neg.get("strategy") if neg else None,
        "approval_block_reason": neg.get("approval_block_reason") if neg else None,
        "invitations": invitations,
        "feedback": feedback,
        "bids": bids,
        "awards": current_awards,
        "orders": current_orders_list,
        "award_ids": list(current_award_ids),
        "order_ids": list(current_order_ids),
        "correlation_id": _correlation_ids.get(negotiation_id, ""),
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    _state[negotiation_id] = state

    # Publish events in chronological order so the frontend renders each
    # step naturally even when multiple events fire in a single poll cycle.

    # 1) Classification change (quadrant / strategy) — earliest step
    if neg:
        prev_quadrant = prev.get("quadrant")
        prev_strategy = prev.get("strategy")
        cur_quadrant = neg.get("kraljic_quadrant")
        cur_strategy = neg.get("strategy")
        if negotiation_id not in _published_classifications and (
            (cur_quadrant and cur_quadrant != prev_quadrant)
            or (cur_strategy and cur_strategy != prev_strategy)
        ):
            _published_classifications.add(negotiation_id)
            logger.info(
                "classification defined negotiation=%s quadrant=%s strategy=%s",
                negotiation_id,
                cur_quadrant,
                cur_strategy,
            )
            await _publish(
                negotiation_id,
                {
                    "event": "classification_defined",
                    "negotiation_id": negotiation_id,
                    "quadrant": cur_quadrant,
                    "strategy": cur_strategy,
                },
            )

    # 2) RFQ sent (supplier invitations)
    # (detected earlier — events from that block fire here)

    # 3) Offer received (newly priced bids)
    for bid_id in new_priced_ids:
        if bid_id in _published_offers:
            continue
        bid = next((b for b in bids if b["bid_id"] == bid_id), None)
        if bid:
            _published_offers.add(bid_id)
            logger.info(
                "offer received negotiation=%s supplier=%s amount=%s source=%s",
                negotiation_id,
                bid.get("supplier_name") or bid.get("supplier_id"),
                bid.get("amount") or bid.get("total_amount"),
                bid.get("source"),
            )
            await _publish(
                negotiation_id,
                {
                    "event": "offer_received",
                    "negotiation_id": negotiation_id,
                    "bid_id": bid.get("bid_id"),
                    "supplier_id": bid.get("supplier_id"),
                    "supplier_name": bid.get("supplier_name"),
                    "amount": bid.get("amount") or bid.get("total_amount"),
                    "unit_price": bid.get("unit_price"),
                    "delivery_days": bid.get("delivery_days"),
                    "currency": bid.get("currency"),
                    "source": bid.get("source"),
                    "status": bid.get("status"),
                    "evaluation_rank": bid.get("evaluation_rank"),
                },
            )

    # 4) Award issued (uses awards/orders fetched earlier)
    prev_award_ids: set[str] = set(prev.get("award_ids", []))
    new_award_ids = current_award_ids - prev_award_ids
    if new_award_ids:
        for a in current_awards:
            if a["award_id"] in new_award_ids and a["award_id"] not in _published_awards:
                _published_awards.add(a["award_id"])
                logger.info(
                    "award issued negotiation=%s award_id=%s supplier=%s amount=%s",
                    negotiation_id,
                    a["award_id"],
                    a.get("supplier_name"),
                    a.get("total_amount"),
                )
                await _publish(
                    negotiation_id,
                    {
                        "event": "award_issued",
                        "negotiation_id": negotiation_id,
                        "award_id": a["award_id"],
                        "supplier_name": a.get("supplier_name"),
                        "total_amount": a.get("total_amount"),
                        "savings_amount": a.get("savings_amount"),
                    },
                )

    # ── Synthetic order ─────────────────────────────────────────────────
    # The orchestrator completes without writing to {env}-test-tenant-orders
    # (the award_comms node is skipped for auto-priced flows, and the normal
    # PO export path is async).  Create a synthetic order so the demo UI
    # shows the PO step.  The next poll cycle detects it and fires po_issued.
    if neg and neg.get("status") == "APPROVED" and current_award_ids:
        if negotiation_id not in _synthetic_neg_ids and not current_orders_list:
            winning_award = current_awards[0] if current_awards else None
            if winning_award:
                # Award may not have supplier_name populated yet — fall back to lookup
                supplier_name = (
                    winning_award.get("supplier_name")
                    or names.get(winning_award.get("supplier_id"))
                    or winning_award.get("supplier_id", "")
                )
                order_id = str(uuid4())
                # Mark BEFORE the await so concurrent poll_once callers see the guard.
                _synthetic_order_ids.add(order_id)
                _synthetic_neg_ids.add(negotiation_id)
                po_payload = {
                    "order_id": order_id,
                    "requisition_id": neg["requisition_id"],
                    "tenant_id": TENANT_ID,
                    "supplier": {
                        "supplier_id": winning_award.get("supplier_id", ""),
                        "name": supplier_name,
                        "supplier_name": supplier_name,
                    },
                    "award": {
                        "award_id": winning_award.get("award_id", ""),
                        "total_amount": float(winning_award.get("total_amount", 0) or 0),
                        "savings_amount": float(winning_award.get("savings_amount", 0) or 0),
                    },
                    "total_price": float(winning_award.get("total_amount", 0) or 0),
                    "currency": winning_award.get("currency", "USD"),
                    "status": "ISSUED",
                }
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(
                            _live_table("test-tenant-orders").put_item,
                            Item={
                                "pk": f"{TENANT_ID}#{order_id}",
                                "sk": "metadata",
                                "order_id": order_id,
                                "requisition_id": neg["requisition_id"],
                                "tenant_id": TENANT_ID,
                                "purchase_order": json.dumps(po_payload),
                                "reception_status": "PENDING",
                                "supplier_name": po_payload["supplier"]["name"],
                                "total_value": str(po_payload["total_price"]),
                                "origin": "demo-harness",
                                "created_at": datetime.now(tz=timezone.utc).isoformat(),
                            },
                        ),
                        timeout=_DDB_THREAD_TIMEOUT,
                    )
                    logger.info(
                        "synthetic order created negotiation=%s order_id=%s total=%s",
                        negotiation_id,
                        order_id,
                        po_payload["total_value"],
                    )
                except (asyncio.TimeoutError, Exception):
                    logger.exception("failed to create synthetic order for %s", negotiation_id)

    # 5) PO issued (reuses current_orders_list fetched above with awards)
    prev_order_ids: set[str] = set(prev.get("order_ids", []))
    current_order_ids = {o["order_id"] for o in current_orders_list if o.get("order_id")}
    new_order_ids = current_order_ids - prev_order_ids
    if new_order_ids:
        for o in current_orders_list:
            if o["order_id"] in new_order_ids and o["order_id"] not in _published_orders:
                _published_orders.add(o["order_id"])
                logger.info(
                    "po issued negotiation=%s order_id=%s total=%s",
                    negotiation_id,
                    o["order_id"],
                    o.get("total_value"),
                )
                await _publish(
                    negotiation_id,
                    {
                        "event": "po_issued",
                        "negotiation_id": negotiation_id,
                        "order_id": o["order_id"],
                        "supplier_name": o.get("supplier_name"),
                        "total_value": o.get("total_value"),
                        "status": o.get("status"),
                    },
                )
    state["order_ids"] = list(current_order_ids)

    # 6) Status change (last — represents the final state of this poll)
    cur_status = neg.get("status") if neg else None
    if cur_status and cur_status != _published_statuses.get(negotiation_id):
        prev_status = _published_statuses.get(negotiation_id)
        _published_statuses[negotiation_id] = cur_status
        logger.info(
            "status change negotiation=%s %s -> %s",
            negotiation_id,
            prev_status,
            cur_status,
        )
        await _publish(
            negotiation_id,
            {
                "event": "status_change",
                "negotiation_id": negotiation_id,
                "status": cur_status,
                "previous_status": prev_status,
            },
        )

    _state[negotiation_id] = state
    return state


async def poll_loop() -> None:
    """Background poll loop — runs for app lifetime."""
    while True:
        active_ids = list(_state.keys())
        # Also discover negotiations from subscribers
        for nid in list(_subscribers.keys()):
            if nid not in active_ids:
                active_ids.append(nid)

        results = await asyncio.gather(
            *(poll_once(nid) for nid in active_ids), return_exceptions=True
        )
        for nid, result in zip(active_ids, results):
            if isinstance(result, Exception):
                logger.exception("poll_once crashed for %s", nid, exc_info=result)

        await asyncio.sleep(OBSERVER_POLL_SECONDS)
