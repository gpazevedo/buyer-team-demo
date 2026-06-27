"""Request body models for API endpoints."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class LineItemRequest(BaseModel):
    item_id: str
    quantity: int
    estimated_price: Optional[float] = None


class CreateRequisitionRequest(BaseModel):
    items: list[LineItemRequest]
    delivery_address: str
    delivery_threshold_days: int
    delivery_ideal_days: Optional[int] = None
    budget_limit_override: Optional[float] = None


class LoadDatasetsRequest(BaseModel):
    datasets: list[str]
