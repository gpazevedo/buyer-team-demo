from fastapi import APIRouter, Depends, HTTPException, Response

from test_tenant_app.auth.jwt import get_tenant_id
from test_tenant_app.clients.dynamo_client import dynamo_client
from test_tenant_app.clients.master_data_client import master_data_client
from test_tenant_app.models import (
    AckOrderRequest,
    PurchaseOrder,
    PurchaseRequisition,
    RejectOrderRequest,
)
from test_tenant_app.negotiation_report import render_negotiation_report_pdf
from test_tenant_app.pdf import render_purchase_order_pdf

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.get("", response_model=list[PurchaseOrder])
def list_orders(tenant_id: str = Depends(get_tenant_id)):
    rows = dynamo_client.get_orders(tenant_id)
    return [PurchaseOrder(**r) for r in rows]


@router.get("/{order_id}", response_model=PurchaseOrder)
def get_order(order_id: str, tenant_id: str = Depends(get_tenant_id)):
    row = dynamo_client.get_order(tenant_id, order_id)
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    return PurchaseOrder(**row)


@router.get("/{order_id}/pdf")
def get_order_pdf(order_id: str, tenant_id: str = Depends(get_tenant_id)):
    row = dynamo_client.get_order(tenant_id, order_id)
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    po = PurchaseOrder(**row)
    return Response(
        content=render_purchase_order_pdf(po),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{po.order_id}.pdf"'},
    )


@router.get("/{order_id}/negotiation-report/pdf")
def get_negotiation_report_pdf(order_id: str, tenant_id: str = Depends(get_tenant_id)):
    row = dynamo_client.get_order(tenant_id, order_id)
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    po = PurchaseOrder(**row)
    pr_row = master_data_client.get_pr(tenant_id, po.requisition_id)
    if not pr_row:
        raise HTTPException(status_code=404, detail="Requisition not found")
    pr = PurchaseRequisition(**pr_row)
    return Response(
        content=render_negotiation_report_pdf(tenant_id, pr, po),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{po.order_id}-negotiation-report.pdf"'
        },
    )


@router.post("/{order_id}/acknowledge", response_model=PurchaseOrder)
def acknowledge_order(
    order_id: str,
    body: AckOrderRequest,
    tenant_id: str = Depends(get_tenant_id),
):
    row = dynamo_client.acknowledge_order(tenant_id, order_id, body.notes)
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    return PurchaseOrder(**row)


@router.post("/{order_id}/reject", response_model=PurchaseOrder)
def reject_order(
    order_id: str,
    body: RejectOrderRequest,
    tenant_id: str = Depends(get_tenant_id),
):
    row = dynamo_client.reject_order(tenant_id, order_id, body.reason)
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    return PurchaseOrder(**row)
