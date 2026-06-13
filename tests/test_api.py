"""Contract-shape tests for every endpoint (stub mode)."""
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SKILL_MODE", "stub")
os.environ.setdefault("AUTH_MODE", "dev")

from test_tenant_app.main import app  # noqa: E402
from test_tenant_app.models import (  # noqa: E402
    DatasetStatus,
    PurchaseRequisition,
    PurchaseOrder,
    Category,
    Supplier,
    Item,
)

client = TestClient(app)


def test_health():
    r = client.get("/healthz")
    assert r.status_code == 200


def test_dataset_status_shape():
    r = client.get("/api/datasets/status")
    assert r.status_code == 200
    DatasetStatus(**r.json())


def test_dataset_load():
    r = client.post("/api/datasets/load", json={"datasets": ["kraljic"]})
    assert r.status_code == 200


def test_dataset_validate():
    r = client.post("/api/datasets/validate")
    assert r.status_code == 200


def test_dataset_reset():
    r = client.post("/api/datasets/reset")
    assert r.status_code == 200


def test_categories_shape():
    r = client.get("/api/categories")
    assert r.status_code == 200
    cats = r.json()
    assert isinstance(cats, list)
    for c in cats:
        Category(**c)


def test_suppliers_shape():
    r = client.get("/api/suppliers")
    assert r.status_code == 200
    for s in r.json():
        Supplier(**s)


def test_items_shape():
    r = client.get("/api/items")
    assert r.status_code == 200
    for i in r.json():
        Item(**i)


def _create_requisition() -> str:
    payload = {
        "items": [{"item_id": "item-001", "quantity": 3}],
        "delivery_address": "123 Test St",
        "delivery_threshold_days": 10,
    }
    r = client.post("/api/requisitions", json=payload)
    assert r.status_code == 201
    pr = PurchaseRequisition(**r.json())
    assert pr.status == "NEW"
    return pr.requisition_id


def test_create_requisition():
    _create_requisition()


def test_get_requisition():
    rid = _create_requisition()
    r = client.get(f"/api/requisitions/{rid}")
    assert r.status_code == 200
    PurchaseRequisition(**r.json())


def test_get_requisition_not_found():
    r = client.get("/api/requisitions/does-not-exist")
    assert r.status_code == 404


def test_cancel_new_requisition():
    rid = _create_requisition()
    r = client.post(f"/api/requisitions/{rid}/cancel")
    assert r.status_code == 200


def test_cancel_active_requires_confirm():
    """Cancelling a stub fixture (status=IN_NEGOTIATION) without confirm returns 409."""
    # Use the stub fixture requisition ID
    r = client.post("/api/requisitions/req-stub-001/cancel")
    assert r.status_code == 409
    body = r.json()
    assert body["detail"]["requires_confirm"] is True


def test_cancel_active_with_confirm():
    r = client.post("/api/requisitions/req-stub-001/cancel?confirm=true")
    assert r.status_code == 200


def test_list_orders_shape():
    r = client.get("/api/orders")
    assert r.status_code == 200
    for o in r.json():
        PurchaseOrder(**o)


def test_get_order():
    r = client.get("/api/orders/po-stub-001")
    assert r.status_code == 200
    PurchaseOrder(**r.json())


def test_get_order_not_found():
    r = client.get("/api/orders/does-not-exist")
    assert r.status_code == 404
