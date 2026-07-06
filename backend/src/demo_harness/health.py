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
)

logger = logging.getLogger("demo_harness.health")


def check_buyer_team() -> dict:
    checks: dict[str, str] = {}

    lambda_client = boto3.client("lambda", region_name=AWS_REGION)
    try:
        lambda_client.get_function(FunctionName=APPROVAL_GATE_FUNCTION)
        checks["approval_gate_lambda"] = "ok"
    except ClientError as e:
        checks["approval_gate_lambda"] = f"error: {e.response['Error']['Code']}"

    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    for label, table_name in (
        ("master_store_table", MASTER_STORE_TABLE),
        ("requisitions_table", REQUISITIONS_TABLE),
    ):
        try:
            ddb.Table(table_name).table_status  # lazy describe_table under the hood
            checks[label] = "ok"
        except ClientError as e:
            checks[label] = f"error: {e.response['Error']['Code']}"

    healthy = all(v == "ok" for v in checks.values())
    if healthy:
        logger.info("Buyer Team health check OK: %s", checks)
    else:
        logger.warning("Buyer Team health check FAILED: %s", checks)
    return {"healthy": healthy, "checks": checks}
