"""Domain entity models — Pydantic v2, source of truth for the OpenAPI contract."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, Field


class KraljicQuadrant(StrEnum):
    NON_CRITICAL = "non_critical"
    LEVERAGE = "leverage"
    BOTTLENECK = "bottleneck"
    STRATEGIC = "strategic"


class PRStatus(StrEnum):
    NEW = "NEW"
    VALIDATED = "VALIDATED"
    ACTIVE = "ACTIVE"
    IN_NEGOTIATION = "IN_NEGOTIATION"
    PENDING_HUMAN_APPROVAL = "PENDING_HUMAN_APPROVAL"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class NegotiationStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Category(BaseModel):
    category_id: str
    tenant_id: str
    name: str
    description: Optional[str] = None
    profit_impact: float
    supply_risk: float
    annual_spend: float
    quadrant: KraljicQuadrant
    supplier_count: int = 0
    item_count: int = 0


class Supplier(BaseModel):
    supplier_id: str
    tenant_id: str
    name: str
    country: Optional[str] = None
    contact_email: Optional[str] = None
    category_ids: list[str] = Field(default_factory=list)
    on_time_delivery_rate: Optional[float] = None
    quality_score: Optional[float] = None


class Item(BaseModel):
    item_id: str
    tenant_id: str
    name: str
    sku: Optional[str] = None
    category_id: str
    category_name: Optional[str] = None
    estimated_price: float
    unit: Optional[str] = None
    preferred_supplier_id: Optional[str] = None


class Negotiation(BaseModel):
    negotiation_id: str
    requisition_id: str
    tenant_id: str
    supplier_id: str
    supplier_name: Optional[str] = None
    status: NegotiationStatus
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class Bid(BaseModel):
    bid_id: str
    requisition_id: str
    negotiation_id: str
    supplier_id: str
    supplier_name: Optional[str] = None
    total_amount: float
    currency: str = "USD"
    lead_time_days: Optional[int] = None
    submitted_at: Optional[datetime] = None
    is_best_bid: bool = False


class Award(BaseModel):
    award_id: str
    requisition_id: str
    bid_id: str
    supplier_id: str
    supplier_name: Optional[str] = None
    total_amount: float
    savings_amount: float
    savings_pct: float
    awarded_at: Optional[datetime] = None


class LineItem(BaseModel):
    item_id: str
    sku: Optional[str] = None
    name: str
    quantity: int
    unit_price: float
    total: float


class PurchaseRequisition(BaseModel):
    requisition_id: str
    tenant_id: str
    status: PRStatus
    items: list[LineItem] = Field(default_factory=list)
    delivery_address: str
    delivery_threshold_days: int
    delivery_ideal_days: Optional[int] = None
    budget_limit: Optional[float] = None
    deadline: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    graph_nodes: dict[str, str] = Field(default_factory=dict)


class PurchaseOrder(BaseModel):
    order_id: str
    requisition_id: str
    tenant_id: str
    supplier_id: str
    supplier_name: Optional[str] = None
    supplier_contact_email: Optional[str] = None
    status: str
    line_items: list[LineItem] = Field(default_factory=list)
    total_value: float
    savings_amount: float
    savings_pct: float
    received_at: datetime
    acknowledged_at: Optional[datetime] = None
    award_id: Optional[str] = None
    trace: Optional[dict] = None
