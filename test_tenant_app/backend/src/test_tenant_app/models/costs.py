"""Result of an on-demand AWS Cost Explorer poll."""

from __future__ import annotations

from pydantic import BaseModel


class CostPollResult(BaseModel):
    period_start: str
    period_end: str
    total_usd: float
    by_service: dict[str, float] = {}
