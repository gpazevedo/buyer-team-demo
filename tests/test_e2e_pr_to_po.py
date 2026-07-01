"""End-to-end lifecycle tests: Purchase Requisition → Purchase Order (stub mode).

State progression in stub mode is wall-clock based; tests drive it deterministically
by backdating the PR's creation timestamp via the `_set_pr_age` seam.
"""

import os

from fastapi.testclient import TestClient

os.environ.setdefault("SKILL_MODE", "stub")
os.environ.setdefault("AUTH_MODE", "dev")

from test_tenant_app.auth.jwt import DEV_TENANT_ID  # noqa: E402
from test_tenant_app.clients.master_data_client import _set_pr_age  # noqa: E402
from test_tenant_app.main import app  # noqa: E402
from test_tenant_app.models import PurchaseOrder, PurchaseRequisition  # noqa: E402

client = TestClient(app)
TENANT = DEV_TENANT_ID


def _create_pr() -> str:
    payload = {
        "items": [
            {"item_id": "item-001", "quantity": 3},
            {"item_id": "item-002", "quantity": 2},
        ],
        "delivery_address": "123 Test St",
        "delivery_threshold_days": 10,
    }
    r = client.post("/api/requisitions", json=payload)
    assert r.status_code == 201
    return r.json()["requisition_id"]


def _status(rid: str) -> str:
    r = client.get(f"/api/requisitions/{rid}")
    assert r.status_code == 200
    return r.json()["status"]


# --- Tier A: state machine ---------------------------------------------------


def test_e2e_pr_progresses_through_states():
    rid = _create_pr()
    assert _status(rid) == "NEW"

    _set_pr_age(TENANT, rid, 7)
    assert _status(rid) == "ACTIVE"

    _set_pr_age(TENANT, rid, 15)
    assert _status(rid) == "IN_NEGOTIATION"

    _set_pr_age(TENANT, rid, 25)
    r = client.get(f"/api/requisitions/{rid}")
    body = r.json()
    assert body["status"] == "PENDING_HUMAN_APPROVAL"
    assert body["graph_nodes"]["spot_bidding"] == "completed"


def test_e2e_negotiations_bids_awards_for_pr():
    rid = _create_pr()
    _set_pr_age(TENANT, rid, 15)
    assert _status(rid) == "IN_NEGOTIATION"

    negs = client.get(f"/api/requisitions/{rid}/negotiations").json()
    assert len(negs) == 2
    assert all(n["requisition_id"] == rid for n in negs)

    bids = client.get(f"/api/requisitions/{rid}/bids").json()
    best = [b for b in bids if b["is_best_bid"]]
    assert len(best) == 1
    assert all(b["requisition_id"] == rid for b in bids)

    awards = client.get(f"/api/requisitions/{rid}/awards").json()
    assert len(awards) == 1
    assert awards[0]["requisition_id"] == rid
    assert awards[0]["total_amount"] == best[0]["total_amount"]


def test_e2e_approve_requires_pending_state():
    rid = _create_pr()
    _set_pr_age(TENANT, rid, 15)  # IN_NEGOTIATION
    assert client.post(f"/api/requisitions/{rid}/approve").status_code == 409

    _set_pr_age(TENANT, rid, 25)  # PENDING_HUMAN_APPROVAL
    r = client.post(f"/api/requisitions/{rid}/approve")
    assert r.status_code == 200
    assert r.json()["status"] == "COMPLETED"

    body = client.get(f"/api/requisitions/{rid}").json()
    assert body["status"] == "COMPLETED"
    assert body["graph_nodes"]["award"] == "completed"


def test_e2e_pending_pr_carries_approval_context():
    rid = _create_pr()
    _set_pr_age(TENANT, rid, 25)  # PENDING_HUMAN_APPROVAL
    body = client.get(f"/api/requisitions/{rid}").json()
    assert body["status"] == "PENDING_HUMAN_APPROVAL"
    ctx = body["approval_context"]
    assert ctx["block_reason"] == "quadrant_strategic"
    assert ctx["quadrant"] == "STRATEGIC"
    assert ctx["quality_score"] == 0.83


def test_e2e_reject_requires_pending_state():
    rid = _create_pr()
    _set_pr_age(TENANT, rid, 15)  # IN_NEGOTIATION
    assert client.post(f"/api/requisitions/{rid}/reject", json={"reason": "x"}).status_code == 409

    _set_pr_age(TENANT, rid, 25)  # PENDING_HUMAN_APPROVAL
    r = client.post(f"/api/requisitions/{rid}/reject", json={"reason": "too expensive"})
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED"
    assert client.get(f"/api/requisitions/{rid}").json()["status"] == "CANCELLED"


def test_e2e_cancel_mid_lifecycle():
    rid = _create_pr()
    _set_pr_age(TENANT, rid, 15)  # IN_NEGOTIATION
    assert _status(rid) == "IN_NEGOTIATION"

    blocked = client.post(f"/api/requisitions/{rid}/cancel")
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["requires_confirm"] is True

    ok = client.post(f"/api/requisitions/{rid}/cancel?confirm=true")
    assert ok.status_code == 200
    assert ok.json()["status"] == "CANCELLED"


# --- Tier B: PR → PO linkage -------------------------------------------------


def _drive_to_completion(rid: str) -> None:
    _set_pr_age(TENANT, rid, 25)
    assert client.post(f"/api/requisitions/{rid}/approve").status_code == 200


def test_e2e_completed_pr_yields_purchase_order():
    rid = _create_pr()
    pr = PurchaseRequisition(**client.get(f"/api/requisitions/{rid}").json())
    _drive_to_completion(rid)

    orders = client.get("/api/orders").json()
    match = [o for o in orders if o["requisition_id"] == rid]
    assert len(match) == 1
    order = PurchaseOrder(**match[0])

    assert len(order.line_items) == len(pr.items)
    assert order.total_value == sum(li.total for li in pr.items)
    assert order.award_id is not None


def test_e2e_order_retrievable_by_id():
    rid = _create_pr()
    _drive_to_completion(rid)

    order_id = next(
        o["order_id"] for o in client.get("/api/orders").json() if o["requisition_id"] == rid
    )
    r = client.get(f"/api/orders/{order_id}")
    assert r.status_code == 200
    assert r.json()["requisition_id"] == rid
