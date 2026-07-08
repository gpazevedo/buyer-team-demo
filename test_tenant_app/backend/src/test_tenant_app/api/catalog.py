"""Catalog endpoints: categories, suppliers, items, negotiations."""

from fastapi import APIRouter, Depends, HTTPException

from test_tenant_app.auth.jwt import get_tenant_id
from test_tenant_app.clients.dynamo_client import dynamo_client
from test_tenant_app.clients.skill_client import skill_client
from test_tenant_app.models import Category, Item, Negotiation, NegotiationDetail, Supplier

router = APIRouter(tags=["catalog"])


@router.get("/api/categories", response_model=list[Category])
def list_categories(tenant_id: str = Depends(get_tenant_id)):
    return [Category(**c) for c in skill_client.get_categories(tenant_id)]


@router.get("/api/categories/{category_id}", response_model=Category)
def get_category(category_id: str, tenant_id: str = Depends(get_tenant_id)):
    cats = skill_client.get_categories(tenant_id)
    match = next((c for c in cats if c["category_id"] == category_id), None)
    if not match:
        raise HTTPException(status_code=404, detail="Category not found")
    return Category(**match)


@router.get("/api/suppliers", response_model=list[Supplier])
def list_suppliers(tenant_id: str = Depends(get_tenant_id)):
    return [Supplier(**s) for s in skill_client.get_suppliers(tenant_id)]


@router.get("/api/suppliers/{supplier_id}", response_model=Supplier)
def get_supplier(supplier_id: str, tenant_id: str = Depends(get_tenant_id)):
    sups = skill_client.get_suppliers(tenant_id)
    match = next((s for s in sups if s["supplier_id"] == supplier_id), None)
    if not match:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return Supplier(**match)


@router.get("/api/items", response_model=list[Item])
def list_items(tenant_id: str = Depends(get_tenant_id)):
    return [Item(**i) for i in skill_client.get_items(tenant_id)]


@router.get("/api/negotiations", response_model=list[Negotiation])
def list_negotiations(tenant_id: str = Depends(get_tenant_id)):
    return [Negotiation(**n) for n in skill_client.get_negotiations(tenant_id)]


@router.get("/api/negotiations/{negotiation_id}", response_model=NegotiationDetail)
def get_negotiation(negotiation_id: str, tenant_id: str = Depends(get_tenant_id)):
    """Negotiation detail: the RFQ→bid→award/rejection communications timeline.

    Bid pricing and every communication in the timeline are simulated — there is no
    real supplier system in this demo (see CommunicationEntry docstring).
    """
    neg = dynamo_client.get_negotiation(tenant_id, negotiation_id)
    if not neg:
        raise HTTPException(status_code=404, detail="Negotiation not found")
    bids = dynamo_client.get_bids_for_negotiation(tenant_id, negotiation_id, neg["requisition_id"])
    comms = dynamo_client.get_communications(tenant_id, negotiation_id)
    return NegotiationDetail(**neg, bids=bids, communications=comms)
