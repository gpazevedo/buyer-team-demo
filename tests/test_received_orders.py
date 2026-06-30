"""PO Inbox reads the PO Receiving domain (`{env}-test-tenant-orders`), PRD-013 §2.

Pins `_normalize_received_order` against the row shape Node 7 / the Skill's
`receive_purchase_order` actually delivers, and that the result validates as the
app's PurchaseOrder contract.
"""

import json
import os

os.environ.setdefault("SKILL_MODE", "stub")
os.environ.setdefault("AUTH_MODE", "dev")

from test_tenant_app.clients.dynamo_client import _normalize_received_order  # noqa: E402
from test_tenant_app.models import PurchaseOrder  # noqa: E402


def _row():
    """A delivered receiving row (mirrors orchestrator/po_delivery.deliver_purchase_order)."""
    po = {
        "order_id": "o1",
        "requisition_id": "r1",
        "tenant_id": "t1",
        "supplier": {"supplier_id": "s1", "name": "Office Pro", "email": "ap@officepro.example"},
        "items": [
            {
                "item_id": "i1",
                "sku": "P",
                "name": "Paper",
                "quantity": 10,
                "unit_price": 8.5,
                "total_price": 85.0,
            }
        ],
        "total_price": 133.0,
        "currency": "USD",
        "negotiation_ids": ["n1"],
        "award": {"award_id": "a1"},
    }
    return {
        "pk": "t1#o1",
        "sk": "metadata",
        "tenant_id": "t1",
        "order_id": "o1",
        "requisition_id": "r1",
        "purchase_order": json.dumps(po),
        "reception_status": "ACKNOWLEDGED",
        "reception_id": "rec1",
        "received_at": "2026-06-27T11:00:00+00:00",
        "acknowledged_at": "2026-06-27T12:00:00+00:00",
        "trace": json.dumps({"requisition_id": "r1", "negotiation_ids": ["n1"], "award_id": "a1"}),
    }


def test_normalize_received_order_validates_contract():
    norm = _normalize_received_order(_row())
    po = PurchaseOrder(**norm)

    assert po.order_id == "o1"
    assert po.requisition_id == "r1"
    assert po.supplier_id == "s1"
    assert po.supplier_name == "Office Pro"
    assert po.supplier_contact_email == "ap@officepro.example"
    assert po.status == "ACKNOWLEDGED"  # receiving status flows straight through
    assert po.total_value == 133.0
    assert po.award_id == "a1"
    assert po.line_items[0].name == "Paper"
    assert po.line_items[0].total == 85.0
    # trace coerces into the typed Trace model
    assert po.trace is not None
    assert po.trace.requisition_id == "r1"
    assert po.trace.negotiation_ids == ["n1"]
    assert po.trace.award_id == "a1"


def test_normalize_received_order_surfaces_acknowledged_at():
    norm = _normalize_received_order(_row())
    assert norm["acknowledged_at"] == "2026-06-27T12:00:00+00:00"


def test_normalize_received_order_surfaces_trace():
    norm = _normalize_received_order(_row())
    assert norm["trace"] == {
        "requisition_id": "r1",
        "negotiation_ids": ["n1"],
        "award_id": "a1",
    }


def test_normalize_received_order_without_acknowledged_at():
    row = _row()
    del row["acknowledged_at"]
    norm = _normalize_received_order(row)
    assert norm["acknowledged_at"] is None


def test_normalize_received_order_without_trace():
    row = _row()
    del row["trace"]
    norm = _normalize_received_order(row)
    assert norm["trace"] is None


def test_normalize_received_order_surfaces_rejection():
    row = _row()
    row["reception_status"] = "REJECTED"
    row["rejected_at"] = "2026-06-27T13:00:00+00:00"
    row["rejection_reason"] = "damaged on arrival"
    norm = _normalize_received_order(row)
    po = PurchaseOrder(**norm)
    assert po.status == "REJECTED"
    assert norm["rejected_at"] == "2026-06-27T13:00:00+00:00"
    assert po.rejection_reason == "damaged on arrival"


def test_normalize_received_order_without_rejection():
    norm = _normalize_received_order(_row())
    assert norm["rejected_at"] is None
    assert norm["rejection_reason"] is None


def _stub_order(**overrides):
    """Build a stub order dict for ack/reject tests."""
    return {
        "order_id": "po-stub-001",
        "requisition_id": "req-stub-001",
        "tenant_id": "6eb4ebaf-804e-5837-ae26-f665a76b58dd",
        "supplier_id": "sup-001",
        "supplier_name": "Staples Business",
        "supplier_contact_email": "procurement@staples.example.com",
        "status": "RECEIVED",
        "line_items": [],
        "total_value": 133.00,
        "savings_amount": 12.00,
        "savings_pct": 8.3,
        "received_at": "2026-06-12T11:00:00Z",
        "award_id": "award-stub-001",
        **overrides,
    }


def test_acknowledge_order_stub():
    from test_tenant_app.clients import master_data_client
    from test_tenant_app.clients.dynamo_client import dynamo_client

    master_data_client._stub_orders.clear()
    master_data_client._stub_orders.append(_stub_order())

    result = dynamo_client.acknowledge_order(
        "6eb4ebaf-804e-5837-ae26-f665a76b58dd", "po-stub-001", notes="received ok"
    )
    assert result is not None
    assert result["status"] == "ACKNOWLEDGED"
    assert result["acknowledged_at"] is not None


def test_reject_order_stub():
    from test_tenant_app.clients import master_data_client
    from test_tenant_app.clients.dynamo_client import dynamo_client

    master_data_client._stub_orders.clear()
    master_data_client._stub_orders.append(_stub_order())

    result = dynamo_client.reject_order(
        "6eb4ebaf-804e-5837-ae26-f665a76b58dd", "po-stub-001", reason="wrong items"
    )
    assert result is not None
    assert result["status"] == "REJECTED"
    assert result["rejection_reason"] == "wrong items"


def test_acknowledge_order_not_found():
    from test_tenant_app.clients import master_data_client
    from test_tenant_app.clients.dynamo_client import dynamo_client

    master_data_client._stub_orders.clear()
    result = dynamo_client.acknowledge_order("nonexistent", "po-none", "")
    assert result is None


def test_reject_order_not_found():
    from test_tenant_app.clients import master_data_client
    from test_tenant_app.clients.dynamo_client import dynamo_client

    master_data_client._stub_orders.clear()
    result = dynamo_client.reject_order("nonexistent", "po-none", "")
    assert result is None
