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
