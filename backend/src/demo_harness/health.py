"""Is Buyer Team actually reachable? Checks the two AWS resources this harness
depends on directly: the Node 6 approval-gate Lambda (S4) and the master-store /
requisitions DynamoDB tables (S1). Not a full platform health check — just the
seams this harness uses.
"""

from __future__ import annotations

import logging

import boto3
from botocore.exceptions import ClientError

from demo_harness.config import (
    APPROVAL_GATE_FUNCTION,
    AWS_REGION,
    MASTER_STORE_TABLE,
    REQUISITIONS_TABLE,
    TENANT_ID,
)

logger = logging.getLogger("demo_harness.health")

_INFORMATIVE_SOURCES = {"auto_priced", "supplier_response_seed"}

_lambda_client = boto3.client("lambda", region_name=AWS_REGION)
_ddb_resource = boto3.resource("dynamodb", region_name=AWS_REGION)


def _classify_pricing_mode(bids: list[dict]) -> dict:
    """bids: already sorted newest-first by priced_at/created_at."""
    for b in bids:
        source = b.get("source", "")
        if source in _INFORMATIVE_SOURCES:
            continue
        if source.endswith("_fallback_stub"):
            return {"pricing_mode": "fallback", "pricing_mode_source": source}
        return {"pricing_mode": "live", "pricing_mode_source": source}
    return {"pricing_mode": "unknown", "pricing_mode_source": None}


def check_buyer_team() -> dict:
    checks: dict[str, str] = {}

    try:
        _lambda_client.get_function(FunctionName=APPROVAL_GATE_FUNCTION)
        checks["approval_gate_lambda"] = "ok"
    except ClientError as e:
        checks["approval_gate_lambda"] = f"error: {e.response['Error']['Code']}"

    for label, table_name in (
        ("master_store_table", MASTER_STORE_TABLE),
        ("requisitions_table", REQUISITIONS_TABLE),
    ):
        try:
            _ddb_resource.Table(table_name).table_status  # lazy describe_table under the hood
            checks[label] = "ok"
        except ClientError as e:
            checks[label] = f"error: {e.response['Error']['Code']}"

    pricing_info = _get_pricing_mode()

    healthy = all(v == "ok" for v in checks.values())
    if healthy:
        logger.info("Buyer Team health check OK: %s", checks)
    else:
        logger.warning("Buyer Team health check FAILED: %s", checks)
    return {"healthy": healthy, "checks": checks, **pricing_info}


def _get_pricing_mode() -> dict:
    try:
        from boto3.dynamodb.conditions import Key
        from test_tenant_app.clients.ddb import table, to_native

        bids = to_native(
            table("bids")
            .query(KeyConditionExpression=Key("tenant_id").eq(TENANT_ID))
            .get("Items", [])
        )
        bids.sort(key=lambda b: b.get("created_at", ""), reverse=True)
        return _classify_pricing_mode(bids[:10])
    except Exception:
        logger.debug("Could not determine pricing mode", exc_info=True)
        return {"pricing_mode": "unknown", "pricing_mode_source": None}
