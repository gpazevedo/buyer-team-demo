"""CostClient — on-demand invoke of the finops cost poller (lambdas/finops_cost_poller).

The Lambda otherwise runs on a daily EventBridge schedule (infra/modules/observability/
finops_cost_poller.tf); this lets the demo app's dashboard trigger the same poll
synchronously, e.g. right before looking at the FinOps CloudWatch dashboard.

SKILL_MODE=stub  canned zero-cost result, no AWS call.
SKILL_MODE=live  invokes the real Lambda (RequestResponse).
"""

from __future__ import annotations

import json
import logging
import os
from functools import cache

import boto3

logger = logging.getLogger("cost_client")
SKILL_MODE = os.getenv("SKILL_MODE", "stub")
REGION = os.getenv("AWS_REGION", "us-east-1")
ENV = os.getenv("ENV", "dev")
COST_POLLER_FUNCTION = os.getenv("COST_POLLER_FUNCTION", f"{ENV}-buyer-team-finops-cost-poller")


@cache
def _lambda():
    return boto3.client("lambda", region_name=REGION)


class CostClient:
    def poll(self) -> dict:
        """Run one Cost-Explorer-to-CloudWatch poll and return its summary."""
        if SKILL_MODE == "stub":
            return {
                "period_start": "1970-01-01T00:00:00+00:00",
                "period_end": "1970-01-01T00:00:00+00:00",
                "total_usd": 0.0,
                "by_service": {},
            }
        resp = _lambda().invoke(
            FunctionName=COST_POLLER_FUNCTION,
            InvocationType="RequestResponse",
            Payload=json.dumps({}).encode(),
        )
        result = json.loads(resp["Payload"].read() or b"{}")
        logger.info(
            "cost poll total_usd=%s services=%d",
            result.get("total_usd"),
            len(result.get("by_service", {})),
        )
        return result


cost_client = CostClient()
