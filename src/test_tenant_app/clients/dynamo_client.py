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
                {"negotiation_id": f"neg-{requisition_id[:8]}-1", "requisition_id": requisition_id, "tenant_id": tenant_id, "supplier_id": "supplier-001", "supplier_name": "Office Pro Supplies", "status": "COMPLETED", "started_at": None, "completed_at": None},
                {"negotiation_id": f"neg-{requisition_id[:8]}-2", "requisition_id": requisition_id, "tenant_id": tenant_id, "supplier_id": "supplier-002", "supplier_name": "Global Office Solutions", "status": "COMPLETED", "started_at": None, "completed_at": None},
            ]
        # Negotiations key on (tenant_id, negotiation_id); query by requisition
        # through the requisition_index GSI (see skills/integration/ingest_pr).
        resp = _live_table("negotiations").query(
            IndexName="requisition_index",
            KeyConditionExpression=Key("requisition_id").eq(requisition_id),
            FilterExpression=Attr("tenant_id").eq(tenant_id),
        )
        return to_native(resp.get("Items", []))

    def get_bids(self, tenant_id: str, requisition_id: str) -> list[dict]:
        if SKILL_MODE == "stub":
            return [
                {"bid_id": f"bid-{requisition_id[:8]}-1", "requisition_id": requisition_id, "negotiation_id": f"neg-{requisition_id[:8]}-1", "supplier_id": "supplier-001", "supplier_name": "Office Pro Supplies", "total_amount": 132.50, "currency": "USD", "lead_time_days": 5, "submitted_at": None, "is_best_bid": True},
                {"bid_id": f"bid-{requisition_id[:8]}-2", "requisition_id": requisition_id, "negotiation_id": f"neg-{requisition_id[:8]}-2", "supplier_id": "supplier-002", "supplier_name": "Global Office Solutions", "total_amount": 145.00, "currency": "USD", "lead_time_days": 7, "submitted_at": None, "is_best_bid": False},
            ]
        # Bids link to a requisition via their negotiation (no requisition GSI).
        neg_ids = {n["negotiation_id"] for n in self.get_negotiations(tenant_id, requisition_id)}
        if not neg_ids:
            return []
        resp = _live_table("bids").query(
            KeyConditionExpression=Key("tenant_id").eq(tenant_id),
        )
        return [b for b in to_native(resp.get("Items", [])) if b.get("negotiation_id") in neg_ids]

    def get_awards(self, tenant_id: str, requisition_id: str) -> list[dict]:
        if SKILL_MODE == "stub":
            return [
                {"award_id": f"award-{requisition_id[:8]}", "requisition_id": requisition_id, "bid_id": f"bid-{requisition_id[:8]}-1", "supplier_id": "supplier-001", "supplier_name": "Office Pro Supplies", "total_amount": 132.50, "savings_amount": 12.50, "savings_pct": 8.6, "awarded_at": None},
            ]
        # Awards link to a requisition via their winning bid (no requisition GSI).
        bid_ids = {b["bid_id"] for b in self.get_bids(tenant_id, requisition_id)}
        if not bid_ids:
            return []
        resp = _live_table("awards").query(
            KeyConditionExpression=Key("tenant_id").eq(tenant_id),
        )
        return [a for a in to_native(resp.get("Items", [])) if a.get("bid_id") in bid_ids]

    def get_orders(self, tenant_id: str) -> list[dict]:
        if SKILL_MODE == "stub":
            from test_tenant_app.clients.master_data_client import _stub_orders

            data = json.loads((_FIXTURES / "orders.json").read_text())
            data += _stub_orders
            return [d for d in data if d["tenant_id"] == tenant_id]
        # Orders key on (pk, sk); no tenant GSI, so scan-filter by tenant_id.
        resp = _live_table("orders").scan(FilterExpression=Attr("tenant_id").eq(tenant_id))
        return to_native(resp.get("Items", []))

    def get_order(self, tenant_id: str, order_id: str) -> dict | None:
        if SKILL_MODE == "stub":
            from test_tenant_app.clients.master_data_client import _stub_orders

            data = json.loads((_FIXTURES / "orders.json").read_text())
            data += _stub_orders
            return next((d for d in data if d["order_id"] == order_id), None)
        item = _live_table("orders").get_item(
            Key={"pk": f"{tenant_id}#{order_id}", "sk": "metadata"}
        ).get("Item")
        return to_native(item) if item else None


dynamo_client = DynamoClient()
