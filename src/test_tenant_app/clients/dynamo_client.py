"""DynamoClient — boto3 against DynamoDB for domain reads (negotiations, bids, awards, orders).

Also used for the items count in dashboard (skill get_dataset_status reports items=0
for the test-tenant because items live under country tenants).

ENV=dev uses dynamodb-local (DYNAMODB_ENDPOINT env var).
"""

from __future__ import annotations

import json
import os
from functools import cache
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Attr, Key

from test_tenant_app.clients.ddb import table as _live_table
from test_tenant_app.clients.ddb import to_native

ENV = os.getenv("ENV", "dev")
REGION = os.getenv("AWS_REGION", "us-east-1")
DYNAMODB_ENDPOINT = os.getenv("DYNAMODB_ENDPOINT", "")  # e.g. http://localhost:8000

_FIXTURES = Path(__file__).parent.parent / "fixtures"
SKILL_MODE = os.getenv("SKILL_MODE", "stub")


@cache
def _ddb():
    kwargs = {"region_name": REGION}
    if DYNAMODB_ENDPOINT:
        kwargs["endpoint_url"] = DYNAMODB_ENDPOINT
    return boto3.resource("dynamodb", **kwargs)


def _table(name: str):
    return _ddb().Table(f"{ENV}-{name}")


def _epoch(v):
    """Coerce a Node-written epoch timestamp (number or numeric string) to int so
    pydantic parses it as a unix datetime; pass ISO strings / None through."""
    if v is None:
        return None
    try:
        return int(float(v))
    except TypeError, ValueError:
        return v


_supplier_names: dict[str, dict[str, str]] = {}


def _supplier_name_map(tenant_id: str) -> dict[str, str]:
    """Cached {supplier_id: name} for a tenant — Node-written bid/award/order rows
    carry only supplier_id; the app contract surfaces supplier_name."""
    if tenant_id not in _supplier_names:
        rows = (
            _live_table("suppliers")
            .query(KeyConditionExpression=Key("tenant_id").eq(tenant_id))
            .get("Items", [])
        )
        _supplier_names[tenant_id] = {r["supplier_id"]: r.get("name") for r in to_native(rows)}
    return _supplier_names[tenant_id]


def _normalize_order(o: dict) -> dict:
    """Map a Node-7-written PO row (`{env}-orders`) to the app's PurchaseOrder
    contract. Node 7 (orchestrator/node_award_comms.py) persists `total_price`,
    `created_at` (epoch), `supplier_id`, and `item_ids`; the app model expects
    `total_value`, `received_at`, `supplier_name`, `line_items`, and savings
    fields (savings are informational, not computed by the award node)."""
    names = _supplier_name_map(o["tenant_id"]) if o.get("tenant_id") else {}
    return {
        **o,
        "total_value": o.get("total_value", o.get("total_price", 0.0)),
        "supplier_name": o.get("supplier_name") or names.get(o.get("supplier_id")),
        "savings_amount": o.get("savings_amount", 0.0),
        "savings_pct": o.get("savings_pct", 0.0),
        "received_at": _epoch(o.get("received_at") or o.get("created_at")),
        "line_items": o.get("line_items", []),
    }


def _normalize_bid(b: dict, requisition_id: str, names: dict[str, str]) -> dict:
    """Map a spot/agent-written bid row to the app's Bid contract: `amount`→
    `total_amount`, `delivery_days`→`lead_time_days`, `evaluation_rank==1`→
    `is_best_bid`; inject `requisition_id` (the row links only via negotiation)."""
    return {
        **b,
        "requisition_id": b.get("requisition_id", requisition_id),
        "supplier_name": b.get("supplier_name") or names.get(b.get("supplier_id")),
        "total_amount": b.get("total_amount", b.get("amount", 0.0)),
        "lead_time_days": b.get("lead_time_days", b.get("delivery_days")),
        "is_best_bid": b.get("is_best_bid", int(b.get("evaluation_rank", 0) or 0) == 1),
        "submitted_at": _epoch(b.get("submitted_at") or b.get("priced_at") or b.get("created_at")),
    }


def _normalize_award(a: dict, names: dict[str, str]) -> dict:
    """Map a Node-written award row to the app's Award contract: `awarded_price`→
    `total_amount`, `winning_bid_id`→`bid_id`, `savings_vs_budget`→`savings_amount`
    (savings_pct derived relative to awarded price)."""
    total = a.get("total_amount", a.get("awarded_price", 0.0))
    savings = a.get("savings_amount", a.get("savings_vs_budget", 0.0))
    return {
        **a,
        "bid_id": a.get("bid_id", a.get("winning_bid_id", "")),
        "supplier_name": a.get("supplier_name") or names.get(a.get("supplier_id")),
        "total_amount": total,
        "savings_amount": savings,
        "savings_pct": a.get("savings_pct", round(savings / total * 100, 1) if total else 0.0),
        "awarded_at": _epoch(a.get("awarded_at") or a.get("created_at")),
    }


# AWARDED is the orchestrator's terminal negotiation status; the app enum tops out
# at COMPLETED. Map the orchestrator vocabulary onto the contract.
_NEG_STATUS = {"AWARDED": "COMPLETED", "ACTIVE": "IN_PROGRESS", "NEGOTIATING": "IN_PROGRESS"}


def _normalize_negotiation(n: dict, names: dict[str, str]) -> dict:
    """Map a Node-written negotiation row to the app's Negotiation contract:
    `candidate_supplier_ids[0]`→`supplier_id`, status vocabulary, epoch timestamps."""
    candidates = n.get("candidate_supplier_ids") or []
    supplier_id = n.get("supplier_id") or (candidates[0] if candidates else "")
    return {
        **n,
        "supplier_id": supplier_id,
        "supplier_name": n.get("supplier_name") or names.get(supplier_id),
        "status": _NEG_STATUS.get(n.get("status"), n.get("status", "PENDING")),
        "started_at": _epoch(n.get("started_at") or n.get("created_at")),
        "completed_at": _epoch(n.get("completed_at") or n.get("approved_at")),
    }


class DynamoClient:
    def count_items(self, tenant_id: str) -> int:
        """Count items for the test tenant (spans country-tenant partitions)."""
        if SKILL_MODE == "stub":
            status = json.loads((_FIXTURES / "dataset_status.json").read_text())
            return status.get("items", 0)
        try:
            resp = _table("items").query(
                KeyConditionExpression="tenant_id = :tid",
                ExpressionAttributeValues={":tid": tenant_id},
                Select="COUNT",
            )
            return resp.get("Count", 0)
        except Exception:
            return 0

    def get_negotiations(self, tenant_id: str, requisition_id: str) -> list[dict]:
        if SKILL_MODE == "stub":
            return [
                {
                    "negotiation_id": f"neg-{requisition_id[:8]}-1",
                    "requisition_id": requisition_id,
                    "tenant_id": tenant_id,
                    "supplier_id": "supplier-001",
                    "supplier_name": "Office Pro Supplies",
                    "status": "COMPLETED",
                    "started_at": None,
                    "completed_at": None,
                },
                {
                    "negotiation_id": f"neg-{requisition_id[:8]}-2",
                    "requisition_id": requisition_id,
                    "tenant_id": tenant_id,
                    "supplier_id": "supplier-002",
                    "supplier_name": "Global Office Solutions",
                    "status": "COMPLETED",
                    "started_at": None,
                    "completed_at": None,
                },
            ]
        # Negotiations key on (tenant_id, negotiation_id); query by requisition
        # through the requisition_index GSI (see skills/integration/ingest_pr).
        resp = _live_table("negotiations").query(
            IndexName="requisition_index",
            KeyConditionExpression=Key("requisition_id").eq(requisition_id),
            FilterExpression=Attr("tenant_id").eq(tenant_id),
        )
        names = _supplier_name_map(tenant_id)
        return [_normalize_negotiation(n, names) for n in to_native(resp.get("Items", []))]

    def get_bids(self, tenant_id: str, requisition_id: str) -> list[dict]:
        if SKILL_MODE == "stub":
            return [
                {
                    "bid_id": f"bid-{requisition_id[:8]}-1",
                    "requisition_id": requisition_id,
                    "negotiation_id": f"neg-{requisition_id[:8]}-1",
                    "supplier_id": "supplier-001",
                    "supplier_name": "Office Pro Supplies",
                    "total_amount": 132.50,
                    "currency": "USD",
                    "lead_time_days": 5,
                    "submitted_at": None,
                    "is_best_bid": True,
                },
                {
                    "bid_id": f"bid-{requisition_id[:8]}-2",
                    "requisition_id": requisition_id,
                    "negotiation_id": f"neg-{requisition_id[:8]}-2",
                    "supplier_id": "supplier-002",
                    "supplier_name": "Global Office Solutions",
                    "total_amount": 145.00,
                    "currency": "USD",
                    "lead_time_days": 7,
                    "submitted_at": None,
                    "is_best_bid": False,
                },
            ]
        # Bids link to a requisition via their negotiation (no requisition GSI).
        neg_ids = {n["negotiation_id"] for n in self.get_negotiations(tenant_id, requisition_id)}
        if not neg_ids:
            return []
        resp = _live_table("bids").query(
            KeyConditionExpression=Key("tenant_id").eq(tenant_id),
        )
        names = _supplier_name_map(tenant_id)
        return [
            _normalize_bid(b, requisition_id, names)
            for b in to_native(resp.get("Items", []))
            if b.get("negotiation_id") in neg_ids
        ]

    def get_awards(self, tenant_id: str, requisition_id: str) -> list[dict]:
        if SKILL_MODE == "stub":
            return [
                {
                    "award_id": f"award-{requisition_id[:8]}",
                    "requisition_id": requisition_id,
                    "bid_id": f"bid-{requisition_id[:8]}-1",
                    "supplier_id": "supplier-001",
                    "supplier_name": "Office Pro Supplies",
                    "total_amount": 132.50,
                    "savings_amount": 12.50,
                    "savings_pct": 8.6,
                    "awarded_at": None,
                },
            ]
        # Awards link to a requisition via their winning bid (no requisition GSI).
        bid_ids = {b["bid_id"] for b in self.get_bids(tenant_id, requisition_id)}
        if not bid_ids:
            return []
        resp = _live_table("awards").query(
            KeyConditionExpression=Key("tenant_id").eq(tenant_id),
        )
        names = _supplier_name_map(tenant_id)
        return [
            _normalize_award(a, names)
            for a in to_native(resp.get("Items", []))
            if a.get("winning_bid_id", a.get("bid_id")) in bid_ids
        ]

    def get_orders(self, tenant_id: str) -> list[dict]:
        if SKILL_MODE == "stub":
            from test_tenant_app.clients.master_data_client import _stub_orders

            data = json.loads((_FIXTURES / "orders.json").read_text())
            data += _stub_orders
            return [d for d in data if d["tenant_id"] == tenant_id]
        # Orders key on (pk, sk); no tenant GSI, so scan-filter by tenant_id.
        resp = _live_table("orders").scan(FilterExpression=Attr("tenant_id").eq(tenant_id))
        return [_normalize_order(o) for o in to_native(resp.get("Items", []))]

    def get_order(self, tenant_id: str, order_id: str) -> dict | None:
        if SKILL_MODE == "stub":
            from test_tenant_app.clients.master_data_client import _stub_orders

            data = json.loads((_FIXTURES / "orders.json").read_text())
            data += _stub_orders
            return next((d for d in data if d["order_id"] == order_id), None)
        item = (
            _live_table("orders")
            .get_item(Key={"pk": f"{tenant_id}#{order_id}", "sk": "metadata"})
            .get("Item")
        )
        if not item:
            return None
        order = _normalize_order(to_native(item))
        # The PO row stores only `item_ids`; surface the ordered line items (names,
        # quantities, prices) from the originating requisition for the detail view.
        if not order["line_items"] and order.get("requisition_id"):
            req = (
                _live_table("requisitions")
                .get_item(Key={"pk": f"{tenant_id}#{order['requisition_id']}", "sk": "metadata"})
                .get("Item")
            )
            if req:
                order["line_items"] = to_native(req).get("items", [])
        return order


dynamo_client = DynamoClient()
