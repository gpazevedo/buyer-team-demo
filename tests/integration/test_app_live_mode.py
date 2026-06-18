"""Live-mode app tests: the FastAPI app driving real DynamoDB (SKILL_MODE=live).

Validates the wired live clients end-to-end through the API:
- catalog reads (categories/suppliers) return real seeded data
- a PR create -> get -> cancel round-trip persists in dev-requisitions

SKILL_MODE=live must be set before importing the app (clients read it at import).
Opt-in via RUN_INTEGRATION=1 (parent conftest) + AWS credentials.
"""
import os

os.environ["SKILL_MODE"] = "live"
os.environ["AUTH_MODE"] = "dev"
os.environ.setdefault("ENV", "dev")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from test_tenant_app.auth.jwt import DEV_TENANT_ID  # noqa: E402
from test_tenant_app.main import app  # noqa: E402
from test_tenant_app.models import Category, Supplier  # noqa: E402

client = TestClient(app)
TENANT = DEV_TENANT_ID


def test_categories_live_returns_real_data():
    r = client.get("/api/categories")
    assert r.status_code == 200
    cats = r.json()
    assert len(cats) >= 20  # test tenant seeded with 20 Kraljic categories
    for c in cats:
        Category(**c)


def test_suppliers_live_returns_real_data():
    r = client.get("/api/suppliers")
    assert r.status_code == 200
    sups = r.json()
    assert len(sups) >= 1
    for s in sups:
        Supplier(**s)


def test_dataset_status_live():
    r = client.get("/api/datasets/status")
    assert r.status_code == 200
    assert r.json()["categories"] >= 20


def test_pr_create_get_cancel_roundtrip_live():
    # Persist directly via the client (not POST /api/requisitions): the create
    # endpoint now fires the live workflow trigger (graph_client.ingest_pr →
    # start_execution), which the doubly-opt-in test_e2e_pr_to_po_live covers.
    # This test stays a focused persistence + cancel roundtrip.
    from test_tenant_app.clients.ddb import table
    from test_tenant_app.clients.master_data_client import master_data_client

    created = master_data_client.create_pr(
        tenant_id=TENANT,
        items=[{"item_id": "item-001", "quantity": 3, "estimated_price": 10.0}],
        delivery_address="123 Live Test St",
        delivery_threshold_days=14,
    )
    rid = created["requisition_id"]
    assert created["status"] == "NEW"

    try:
        # Persisted and readable back from DynamoDB.
        got = client.get(f"/api/requisitions/{rid}")
        assert got.status_code == 200
        assert got.json()["requisition_id"] == rid

        # Cancel updates the persisted row.
        cancelled = client.post(f"/api/requisitions/{rid}/cancel")
        assert cancelled.status_code == 200
        assert client.get(f"/api/requisitions/{rid}").json()["status"] == "CANCELLED"
    finally:
        table("requisitions").delete_item(Key={"pk": f"{TENANT}#{rid}", "sk": "metadata"})
