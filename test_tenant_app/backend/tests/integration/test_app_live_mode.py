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


def test_pr_create_writes_master_store_live():
    # Canonical seam: create_pr writes a NEW PR to the tenant master store
    # ({env}-test-tenant-master-purchase-requisitions). The DynamoDB Stream →
    # pr-event-router → ingest_pr chain (covered by the doubly-opt-in
    # test_e2e_pr_to_po_live) then drives the workflow — the app no longer triggers
    # Step Functions directly. This test stays a focused master-store persistence check.
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

    master = table("test-tenant-master-purchase-requisitions")
    try:
        row = master.get_item(Key={"tenant_id": TENANT, "requisition_id": rid}).get("Item")
        assert row is not None, "PR not written to the master store"
        assert row["status"] == "NEW"
        # The sort keys the emulator's list tools page on are present.
        assert row["status_sk"].startswith("NEW#")
        assert row["lm_sk"].endswith(f"#{rid}")
    finally:
        master.delete_item(Key={"tenant_id": TENANT, "requisition_id": rid})
