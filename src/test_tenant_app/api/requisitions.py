from fastapi import APIRouter, Depends, HTTPException, Query, status

from test_tenant_app.auth.jwt import Approver, get_approver, get_tenant_id
from test_tenant_app.clients.dynamo_client import dynamo_client
from test_tenant_app.clients.master_data_client import master_data_client
from test_tenant_app.clients.skill_client import skill_client
from test_tenant_app.models import (
    Award,
    Bid,
    CreateRequisitionRequest,
    Negotiation,
    PurchaseRequisition,
    RejectRequisitionRequest,
)

router = APIRouter(prefix="/api/requisitions", tags=["requisitions"])

_ACTIVE_STATUSES = {"IN_NEGOTIATION", "ACTIVE"}


@router.post("", response_model=PurchaseRequisition, status_code=status.HTTP_201_CREATED)
def create_requisition(
    body: CreateRequisitionRequest,
    tenant_id: str = Depends(get_tenant_id),
):
    items_raw = skill_client.get_items(tenant_id)
    items_map = {i["item_id"]: i for i in items_raw}
    items = [
        {
            **items_map.get(it.item_id, {}),
            "item_id": it.item_id,
            "quantity": it.quantity,
            "estimated_price": it.estimated_price
            or items_map.get(it.item_id, {}).get("estimated_price", 0),
        }
        for it in body.items
    ]
    # Canonical path: persisting the PR to the master store is the trigger — its
    # DynamoDB Stream drives pr_event_router → ingest_pr → start_negotiation_workflow.
    # No direct app→Step Functions call.
    pr = master_data_client.create_pr(
        tenant_id=tenant_id,
        items=items,
        delivery_address=body.delivery_address,
        delivery_threshold_days=body.delivery_threshold_days,
        delivery_ideal_days=body.delivery_ideal_days,
        budget_limit=body.budget_limit_override,
    )
    return PurchaseRequisition(**pr)


@router.get("/{requisition_id}", response_model=PurchaseRequisition)
def get_requisition(requisition_id: str, tenant_id: str = Depends(get_tenant_id)):
    pr = master_data_client.get_pr(tenant_id, requisition_id)
    if not pr:
        raise HTTPException(status_code=404, detail="Requisition not found")
    return PurchaseRequisition(**pr)


@router.get("/{requisition_id}/negotiations", response_model=list[Negotiation])
def get_negotiations(requisition_id: str, tenant_id: str = Depends(get_tenant_id)):
    rows = dynamo_client.get_negotiations(tenant_id, requisition_id)
    return [Negotiation(**r) for r in rows]


@router.get("/{requisition_id}/bids", response_model=list[Bid])
def get_bids(requisition_id: str, tenant_id: str = Depends(get_tenant_id)):
    rows = dynamo_client.get_bids(tenant_id, requisition_id)
    return [Bid(**r) for r in rows]


@router.get("/{requisition_id}/awards", response_model=list[Award])
def get_awards(requisition_id: str, tenant_id: str = Depends(get_tenant_id)):
    rows = dynamo_client.get_awards(tenant_id, requisition_id)
    return [Award(**r) for r in rows]


@router.post("/{requisition_id}/approve")
def approve_requisition(
    requisition_id: str,
    approver: Approver = Depends(get_approver),
):
    tenant_id = approver["tenant_id"]
    pr = master_data_client.get_pr(tenant_id, requisition_id)
    if not pr:
        raise HTTPException(status_code=404, detail="Requisition not found")
    if pr["status"] != "PENDING_HUMAN_APPROVAL":
        raise HTTPException(status_code=409, detail="PR is not pending approval")
    return master_data_client.approve_pr(tenant_id, requisition_id, approver)


@router.post("/{requisition_id}/reject")
def reject_requisition(
    requisition_id: str,
    body: RejectRequisitionRequest,
    approver: Approver = Depends(get_approver),
):
    tenant_id = approver["tenant_id"]
    pr = master_data_client.get_pr(tenant_id, requisition_id)
    if not pr:
        raise HTTPException(status_code=404, detail="Requisition not found")
    if pr["status"] != "PENDING_HUMAN_APPROVAL":
        raise HTTPException(status_code=409, detail="PR is not pending approval")
    return master_data_client.reject_pr(tenant_id, requisition_id, body.reason, approver)


@router.post("/{requisition_id}/cycle_back")
def cycle_back_requisition(
    requisition_id: str,
    approver: Approver = Depends(get_approver),
):
    tenant_id = approver["tenant_id"]
    pr = master_data_client.get_pr(tenant_id, requisition_id)
    if not pr:
        raise HTTPException(status_code=404, detail="Requisition not found")
    if pr["status"] != "PENDING_HUMAN_APPROVAL":
        raise HTTPException(status_code=409, detail="PR is not pending approval")
    return master_data_client.cycle_back_pr(tenant_id, requisition_id, approver)


@router.post("/{requisition_id}/cancel")
def cancel_requisition(
    requisition_id: str,
    confirm: bool = Query(default=False),
    tenant_id: str = Depends(get_tenant_id),
):
    pr = master_data_client.get_pr(tenant_id, requisition_id)
    if not pr:
        raise HTTPException(status_code=404, detail="Requisition not found")

    if pr["status"] in _ACTIVE_STATUSES and not confirm:
        raise HTTPException(
            status_code=409,
            detail={"error": "PR has active negotiations", "requires_confirm": True},
        )

    return master_data_client.cancel_pr(tenant_id, requisition_id)
