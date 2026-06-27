"""Dataset status and configuration models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel


class PhaseLoadStatus(StrEnum):
    NOT_LOADED = "not_loaded"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class PhaseStatus(BaseModel):
    phase: str
    status: PhaseLoadStatus
    entities_created: int = 0
    last_loaded_at: Optional[datetime] = None


class KraljicThresholds(BaseModel):
    profit_impact: float = 0.5
    supply_risk: float = 0.5


class DatasetStatus(BaseModel):
    tenant_id: str
    categories: int = 0
    suppliers: int = 0
    items: int = 0
    negotiations: int = 0
    phases: list[PhaseStatus] = []
    thresholds: KraljicThresholds = KraljicThresholds()
    last_loaded_at: Optional[datetime] = None
    all_phases_complete: bool = False
