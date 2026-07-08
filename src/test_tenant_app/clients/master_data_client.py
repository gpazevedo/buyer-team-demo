"""MasterDataClient — reads/writes via dynamodb-master-data and tenant-mdm-emulator
MCP servers (PRD-015 §4).

SKILL_MODE=stub  returns fixture data.
SKILL_MODE=live  canonical event-driven path: create_pr writes the PR to the tenant
                 master store ({env}-test-tenant-master-purchase-requisitions) at
                 status NEW. The table's DynamoDB Stream → pr_event_router →
                 ingest_pr → start_negotiation_workflow chain drives the workflow
                 (no direct app→Step Functions trigger). See IMPLEMENTATION_PLAN.md
                 "return to the canonical design".
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

SKILL_MODE = os.getenv("SKILL_MODE", "stub")
ENV = os.getenv("ENV", "dev")
DEFAULT_TENANT_ID = os.getenv("DEV_TENANT_ID", "6eb4ebaf-804e-5837-ae26-f665a76b58dd")

# In-memory stores for stub mode
_stub_requisitions: dict[str, dict] = {}  # keyed by tenant_id:requisition_id
_stub_orders: list[dict] = []  # POs emitted on approval


def _set_pr_age(tenant_id: str, requisition_id: str, seconds: float) -> None:
    """Test/demo seam: backdate a stub PR so state progression can be driven deterministically."""
    pr = _stub_requisitions.get(f"{tenant_id}:{requisition_id}")
    if pr:
        pr["_created_ts"] = time.time() - seconds


def _advance_stub_state(pr: dict) -> dict:
    """Simulate time-based state progression for demo polling."""
    if pr["status"] in (
        "CANCELLED",
        "COMPLETED",
        "FAILED",
        "PENDING_HUMAN_APPROVAL",
        "REQUIRES_ATTENTION",
    ):
        return pr
    elapsed = time.time() - pr.get("_created_ts", time.time())
    if elapsed < 5:
        pr["status"] = "NEW"
        pr["graph_nodes"] = {}
    elif elapsed < 10:
        pr["status"] = "ACTIVE"
        pr["graph_nodes"] = {"ingest": "completed"}
    elif elapsed < 20:
        pr["status"] = "IN_NEGOTIATION"
        pr["graph_nodes"] = {
            "ingest": "completed",
            "spot_bidding": "in_progress",
            "bid_evaluation": "pending",
        }
    else:
        pr["status"] = "PENDING_HUMAN_APPROVAL"
        pr["graph_nodes"] = {
            "ingest": "completed",
            "spot_bidding": "completed",
            "bid_evaluation": "completed",
            "award": "pending",
        }
        # Demo "why paused" context (Node 6 persists these live; here they're synthesized).
        pr["approval_context"] = {
            "block_reason": "quadrant_strategic",
            "quadrant": "STRATEGIC",
            "quality_score": 0.83,
            "awarded_price": 132.50,
        }
    pr["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
    return pr


def _build_order(tenant_id: str, pr: dict) -> dict:
    """Build a PurchaseOrder from an approved PR and its (stub) award."""
    from test_tenant_app.clients.dynamo_client import dynamo_client

    requisition_id = pr["requisition_id"]
    awards = dynamo_client.get_awards(tenant_id, requisition_id)
    award = awards[0] if awards else {}
    line_items = pr["items"]
    return {
        "order_id": f"po-{requisition_id[:8]}",
        "requisition_id": requisition_id,
        "tenant_id": tenant_id,
        "supplier_id": award.get("supplier_id", "supplier-001"),
        "supplier_name": award.get("supplier_name"),
        "supplier_contact_email": None,
        "status": "RECEIVED",
        "line_items": line_items,
        "total_value": sum(li["total"] for li in line_items),
        "savings_amount": award.get("savings_amount", 0.0),
        "savings_pct": award.get("savings_pct", 0.0),
        "received_at": datetime.now(tz=timezone.utc).isoformat(),
        "award_id": award.get("award_id"),
    }


def _master_pr_item(tenant_id: str, requisition_id: str, pr: dict, created: datetime) -> dict:
    """Build the canonical tenant master-store PR row (PRD-015 §3).

    Keys: tenant_id (hash) / requisition_id (range). Carries the lm_idx/status_idx
    sort keys the emulator's list tools page on, plus the fields ingest_pr copies
    into the domain {env}-requisitions row. Items keep the app's rich line-item
    shape — Node 1 normalizes bare-id and rich-dict items alike."""
    ts = created.isoformat()
    return {
        "tenant_id": tenant_id,
        "requisition_id": requisition_id,
        "status": "NEW",
        "last_modified": ts,
        "lm_sk": f"{ts}#{requisition_id}",
        "status_sk": f"NEW#{ts}#{requisition_id}",
        "created_at": pr["created_at"],
        "items": pr["items"],
        "delivery_address": pr["delivery_address"],
        "delivery_threshold_days": pr["delivery_threshold_days"],
        "delivery_ideal_days": pr["delivery_ideal_days"],
        "budget_override": pr.get("budget_limit"),
        "source_system": "test-tenant-app",
    }


# Negotiation strategy → the strategy node label shown on the PR Tracker.
_STRATEGY_NODE = {
    "SPOT_BID": "spot_bidding",
    "LEVERAGE_AUCTION": "leverage_auction",
    "BOTTLENECK_NEGOTIATION": "bottleneck_negotiation",
    "STRATEGIC_PARTNERSHIP": "strategic_partnership",
}


def _synthesize_graph_nodes(tenant_id: str, pr: dict) -> dict:
    """Derive the workflow-node states the PR Tracker renders from live signals.

    Orchestrator nodes only write `ingest_validate` into the requisition row, so the
    panel would otherwise show a single node. Reconstruct the rest (lowercase states
    the frontend colour-maps) from the negotiation lifecycle + the requisition's own
    `order_ids`, without depending on every node back-writing its progress."""
    from test_tenant_app.clients.dynamo_client import dynamo_client

    status = pr.get("status")
    if status in ("CANCELLED",):
        return {"ingest": "completed"}
    nodes = {"ingest": "completed"}
    negs = dynamo_client.get_negotiations(tenant_id, pr["requisition_id"])
    if not negs:
        return nodes  # ingest only; negotiation not created yet
    neg = negs[0]
    has_order = bool(pr.get("order_ids"))
    approval = (neg.get("approval_decision") or neg.get("approval_status") or "").upper()
    awarded = neg.get("status") == "COMPLETED" or has_order  # normalized AWARDED→COMPLETED
    failed = status == "FAILED"

    nodes["kraljic"] = "completed" if neg.get("kraljic_quadrant") else "in_progress"
    strat_node = _STRATEGY_NODE.get(neg.get("strategy"), "negotiation")
    nodes[strat_node] = "completed" if awarded else "in_progress"
    nodes["bid_evaluation"] = (
        "completed" if awarded else ("in_progress" if not failed else "failed")
    )
    if status == "PENDING_HUMAN_APPROVAL":
        nodes["approval"] = "in_progress"
    elif approval in ("REJECTED",):
        nodes["approval"] = "failed"
    elif approval in ("AUTO_APPROVED", "APPROVED"):
        nodes["approval"] = "completed"
    else:
        nodes["approval"] = "pending"
    nodes["award"] = (
        "completed"
        if has_order
        else ("in_progress" if nodes["approval"] == "completed" else "pending")
    )
    return nodes


def _approval_context(tenant_id: str, pr: dict) -> dict | None:
    """Surface *why* the PR is paused for human approval (PRD-002 §3.6).

    Node 6 persists `approval_block_reason`/`approval_quality_score` on the negotiation
    row; `kraljic_quadrant`/`awarded_price` already live there. Fold them into the read
    model so the approver reviews with the block reason + award context in hand."""
    from test_tenant_app.clients.dynamo_client import dynamo_client

    negs = dynamo_client.get_negotiations(tenant_id, pr["requisition_id"])
    if not negs:
        return None
    neg = negs[0]
    return {
        "block_reason": neg.get("approval_block_reason"),
        "quadrant": neg.get("kraljic_quadrant"),
        "quality_score": neg.get("approval_quality_score"),
        "awarded_price": neg.get("awarded_price"),
    }


class MasterDataClient:
    def create_pr(
        self,
        tenant_id: str,
        items: list[dict],
        delivery_address: str,
        delivery_threshold_days: int,
        delivery_ideal_days: int | None = None,
        budget_limit: float | None = None,
        correlation_id: str | None = None,
    ) -> dict:
        requisition_id = str(uuid4())
        now = datetime.now(tz=timezone.utc)
        deadline = now + timedelta(days=delivery_threshold_days)
        computed_budget = budget_limit or sum(
            i.get("estimated_price", 0) * i.get("quantity", 1) for i in items
        )
        pr = {
            "requisition_id": requisition_id,
            "tenant_id": tenant_id,
            "status": "NEW",
            "items": [
                {
                    "item_id": it["item_id"],
                    "sku": it.get("sku"),
                    "name": it.get("name", ""),
                    "quantity": it.get("quantity", 1),
                    "unit_price": it.get("estimated_price", 0),
                    "total": it.get("estimated_price", 0) * it.get("quantity", 1),
                }
                for it in items
            ],
            "delivery_address": delivery_address,
            "delivery_threshold_days": delivery_threshold_days,
            "delivery_ideal_days": delivery_ideal_days,
            "budget_limit": computed_budget,
            "deadline": deadline.isoformat(),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "graph_nodes": {"correlation_id": correlation_id} if correlation_id else {},
        }
        if SKILL_MODE == "stub":
            pr["_created_ts"] = time.time()
            _stub_requisitions[f"{tenant_id}:{requisition_id}"] = pr
            return pr
        # live (canonical): write the PR to the tenant master store at status NEW.
        # The table's DynamoDB Stream → pr_event_router → ingest_pr upserts the domain
        # {env}-requisitions row (VALIDATED) and starts the negotiation workflow — the
        # app does NOT trigger Step Functions directly.
        from test_tenant_app.clients.ddb import table, to_decimal

        table("test-tenant-master-purchase-requisitions").put_item(
            Item=to_decimal(_master_pr_item(tenant_id, requisition_id, pr, now))
        )
        return pr

    def get_pr(self, tenant_id: str, requisition_id: str) -> dict | None:
        if SKILL_MODE == "stub":
            pr = _stub_requisitions.get(f"{tenant_id}:{requisition_id}")
            if pr:
                return _advance_stub_state(pr)
            # If no in-memory PR, return stub fixture for demo
            import json
            from pathlib import Path

            data = json.loads(
                (Path(__file__).parent.parent / "fixtures" / "requisition.json").read_text()
            )
            if data["requisition_id"] == requisition_id:
                return data
            return None
        from test_tenant_app.clients.ddb import table, to_native

        item = (
            table("requisitions")
            .get_item(Key={"pk": f"{tenant_id}#{requisition_id}", "sk": "metadata"})
            .get("Item")
        )
        if not item:
            return None
        pr = to_native(item)
        # The canonical-path domain row (pr_event_router ingest + orchestrator node
        # writes) carries created_at but not updated_at; the app contract requires it.
        # Normalize at the read boundary (same seam as the other live-mode field gaps).
        pr.setdefault("updated_at", pr.get("created_at"))
        pr["graph_nodes"] = _synthesize_graph_nodes(tenant_id, pr)
        if pr.get("status") == "PENDING_HUMAN_APPROVAL":
            pr["approval_context"] = _approval_context(tenant_id, pr)
        return pr

    def list_prs(self, tenant_id: str, status: str | None = None) -> list[dict]:
        """List a tenant's requisitions, optionally filtered by status (the approval
        inbox passes PENDING_HUMAN_APPROVAL). Matching rows carry graph_nodes +
        approval_context, same as get_pr, so the inbox can show *why* each is paused."""
        if SKILL_MODE == "stub":
            out = []
            for key, pr in _stub_requisitions.items():
                if not key.startswith(f"{tenant_id}:"):
                    continue
                pr = _advance_stub_state(pr)
                if status and pr["status"] != status:
                    continue
                out.append(pr)
            return out
        from boto3.dynamodb.conditions import Attr

        from test_tenant_app.clients.ddb import table, to_native

        resp = table("requisitions").scan(
            FilterExpression=Attr("pk").begins_with(f"{tenant_id}#") & Attr("sk").eq("metadata")
        )
        out = []
        for pr in to_native(resp.get("Items", [])):
            if status and pr.get("status") != status:
                continue
            pr.setdefault("updated_at", pr.get("created_at"))
            pr["graph_nodes"] = _synthesize_graph_nodes(tenant_id, pr)
            if pr.get("status") == "PENDING_HUMAN_APPROVAL":
                pr["approval_context"] = _approval_context(tenant_id, pr)
            out.append(pr)
        out.sort(key=lambda p: p.get("created_at", ""), reverse=True)
        return out

    def approve_pr(self, tenant_id: str, requisition_id: str, approver: dict | None = None) -> dict:
        if SKILL_MODE == "stub":
            key = f"{tenant_id}:{requisition_id}"
            pr = _stub_requisitions.get(key)
            if pr:
                pr["status"] = "COMPLETED"
                pr["graph_nodes"]["award"] = "completed"
                pr["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
                _stub_orders.append(_build_order(tenant_id, pr))
            return {"status": "COMPLETED", "requisition_id": requisition_id}
        # live: release the paused Approval Gate token; Node 7 issues the PO and
        # sets the requisition COMPLETED — the API must not pre-empt that here.
        from test_tenant_app.clients.graph_client import graph_client

        return graph_client.approve_award(tenant_id, requisition_id, approver=approver)

    def reject_pr(
        self, tenant_id: str, requisition_id: str, reason: str = "", approver: dict | None = None
    ) -> dict:
        """Approver rejects the pending award (HITL REJECTED). Node 6's REJECTED
        path cancels the negotiation + requisition, so the API must not pre-empt it."""
        if SKILL_MODE == "stub":
            key = f"{tenant_id}:{requisition_id}"
            pr = _stub_requisitions.get(key)
            if pr:
                pr["status"] = "CANCELLED"
                pr["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
            return {"status": "CANCELLED", "requisition_id": requisition_id}
        from test_tenant_app.clients.graph_client import graph_client

        return graph_client.reject_award(
            tenant_id, requisition_id, reason=reason or "Approver rejected", approver=approver
        )

    def cycle_back_pr(
        self, tenant_id: str, requisition_id: str, approver: dict | None = None
    ) -> dict:
        """Approver sends the award back for re-negotiation (HITL CYCLE_BACK). Node 6
        re-runs the strategy once, then hands off to REQUIRES_ATTENTION when exhausted."""
        if SKILL_MODE == "stub":
            pr = _stub_requisitions.get(f"{tenant_id}:{requisition_id}")
            if pr:
                if pr.get("_cycle_back_count", 0) >= 1:
                    pr["status"] = "REQUIRES_ATTENTION"  # exhausted (mirrors Node 6 REQ-G204)
                else:
                    pr["_cycle_back_count"] = pr.get("_cycle_back_count", 0) + 1
                    pr["status"] = "IN_NEGOTIATION"  # re-run; will re-pause on its own
                    pr["_created_ts"] = time.time() - 15
                pr.pop("approval_context", None)
                pr["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
                return {"status": pr["status"], "requisition_id": requisition_id}
            return {"status": "REQUIRES_ATTENTION", "requisition_id": requisition_id}
        from test_tenant_app.clients.graph_client import graph_client

        return graph_client.cycle_back_award(tenant_id, requisition_id, approver=approver)

    def cancel_pr(self, tenant_id: str, requisition_id: str) -> dict:
        if SKILL_MODE == "stub":
            key = f"{tenant_id}:{requisition_id}"
            pr = _stub_requisitions.get(key)
            if pr:
                pr["status"] = "CANCELLED"
                pr["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
            return {"status": "CANCELLED", "requisition_id": requisition_id}
        # live: release any paused approval token as REJECTED so the execution does
        # not hang (no-op if nothing is paused), then mark the requisition CANCELLED.
        from test_tenant_app.clients.graph_client import graph_client

        graph_client.reject_award(tenant_id, requisition_id, reason="tenant_cancelled")
        self._set_status(tenant_id, requisition_id, "CANCELLED")
        return {"status": "CANCELLED", "requisition_id": requisition_id}

    def _set_status(self, tenant_id: str, requisition_id: str, status: str) -> None:
        from test_tenant_app.clients.ddb import table

        table("requisitions").update_item(
            Key={"pk": f"{tenant_id}#{requisition_id}", "sk": "metadata"},
            UpdateExpression="SET #s = :s, updated_at = :u",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": status,
                ":u": datetime.now(tz=timezone.utc).isoformat(),
            },
        )

    def get_thresholds(self, env: str) -> dict:
        """Read Kraljic thresholds from {env}-system-config.

        Table key: config_group (hash) / config_key (range).
        Value stored in config_json blob.
        """
        if SKILL_MODE == "stub":
            return {"profit_impact": 0.5, "supply_risk": 0.5}
        # live: read from DynamoDB {env}-system-config
        import json as _json

        import boto3

        ddb = boto3.resource("dynamodb", region_name=os.getenv("AWS_REGION", "us-east-1"))
        table = ddb.Table(f"{env}-system-config")
        resp = table.get_item(Key={"config_group": "kraljic", "config_key": "thresholds"})
        item = resp.get("Item")
        if item:
            return _json.loads(item["config_json"])
        return {"profit_impact": 0.5, "supply_risk": 0.5}


master_data_client = MasterDataClient()
