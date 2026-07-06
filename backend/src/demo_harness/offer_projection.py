"""Supplier-Offer Projection — polls {env}-communications + {env}-bids and
joins them into a per-supplier offer view (Seams S2/S3, PRD-020 §5.2).

Design 1 only (observed offers). Read-only — never writes to {env}-bids.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from boto3.dynamodb.conditions import Key
from test_tenant_app.clients.ddb import table as _live_table
from test_tenant_app.clients.ddb import to_native
from test_tenant_app.clients.dynamo_client import _supplier_name_map, dynamo_client

from demo_harness.config import (
    OBSERVER_POLL_SECONDS,
    TENANT_ID,
)

logger = logging.getLogger("demo_harness.offer_projection")

# In-memory projection: negotiation_id -> {suppliers, offers, invitations, status}
_state: dict[str, dict] = {}

# SSE subscribers: {negotiation_id: [asyncio.Queue]}
_subscribers: dict[str, list[asyncio.Queue]] = {}
_global_subscribers: list[asyncio.Queue] = []  # for all-negotiation listeners


def get_state(negotiation_id: str | None = None) -> dict | list[dict]:
    """Return current projection state for one or all negotiations."""
    if negotiation_id:
        return _state.get(negotiation_id)
    return list(_state.values())


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


def _query_communications(negotiation_id: str) -> list[dict]:
    """Read communications for one negotiation from DynamoDB."""
    entries = dynamo_client.get_communications(TENANT_ID, negotiation_id)
    return [e for e in entries if e.get("type") == "BID_INVITATION"]


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


async def poll_once(negotiation_id: str) -> dict | None:
    """Poll all data sources for one negotiation; update projection state.
    Returns the updated state dict, or None if nothing changed.
    """
    try:
        neg = _query_negotiation(negotiation_id)
        bids = _query_bids(negotiation_id)
        invitations = _query_communications(negotiation_id)
    except Exception:
        logger.exception("poll failed for %s", negotiation_id)
        return None

    if not neg and negotiation_id not in _state:
        return None

    prev = _state.get(negotiation_id, {})
    prev_bids = prev.get("bids", [])

    # Detect new or changed bids
    priced_bids = [b for b in bids if b.get("total_amount") or b.get("amount")]
    prev_priced = {b["bid_id"] for b in prev_bids if b.get("total_amount") or b.get("amount")}
    current_priced = {b["bid_id"] for b in priced_bids}

    new_priced = current_priced - prev_priced

    names = _supplier_name_map(TENANT_ID)
    for b in bids:
        b.setdefault("supplier_name", names.get(b.get("supplier_id")))

    state = {
        "negotiation_id": negotiation_id,
        "requisition_id": neg.get("requisition_id") if neg else None,
        "status": neg.get("status") if neg else None,
        "quadrant": neg.get("kraljic_quadrant") if neg else None,
        "strategy": neg.get("strategy") if neg else None,
        "approval_block_reason": neg.get("approval_block_reason") if neg else None,
        "invitations": invitations,
        "bids": bids,
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    _state[negotiation_id] = state

    # Publish events for newly issued POs
    prev_order_ids: set[str] = set(prev.get("order_ids", []))
    current_orders = (
        dynamo_client.get_orders(TENANT_ID) if neg and neg.get("requisition_id") else []
    )
    current_order_ids = {o["order_id"] for o in current_orders if o.get("order_id")}
    new_order_ids = current_order_ids - prev_order_ids
    if new_order_ids:
        for o in current_orders:
            if o["order_id"] in new_order_ids:
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

    # Publish events for newly priced bids
    for bid_id in new_priced:
        bid = next((b for b in bids if b["bid_id"] == bid_id), None)
        if bid:
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
                    "supplier_id": bid.get("supplier_id"),
                    "supplier_name": bid.get("supplier_name"),
                    "amount": bid.get("amount") or bid.get("total_amount"),
                    "unit_price": bid.get("unit_price"),
                    "delivery_days": bid.get("delivery_days"),
                    "currency": bid.get("currency"),
                    "source": bid.get("source"),
                    "status": bid.get("status"),
                },
            )

    # Publish status change
    prev_status = prev.get("status")
    if neg and neg.get("status") != prev_status:
        logger.info(
            "status change negotiation=%s %s -> %s",
            negotiation_id,
            prev_status,
            neg.get("status"),
        )
        await _publish(
            negotiation_id,
            {
                "event": "status_change",
                "negotiation_id": negotiation_id,
                "status": neg.get("status"),
                "previous_status": prev_status,
            },
        )

    return state


async def poll_loop() -> None:
    """Background poll loop — runs for app lifetime."""
    while True:
        active_ids = list(_state.keys())
        # Also discover negotiations from subscribers
        for nid in list(_subscribers.keys()):
            if nid not in active_ids:
                active_ids.append(nid)

        for nid in active_ids:
            await poll_once(nid)

        await asyncio.sleep(OBSERVER_POLL_SECONDS)
