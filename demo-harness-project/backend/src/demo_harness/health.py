"""Is Buyer Team actually reachable? Checks the AWS resources this harness
depends on directly: the Node 6 approval-gate Lambda (S4), the master-store /
requisitions DynamoDB tables (S1), and the buyer-team Step Functions state
machine. Not a full platform health check — just the seams this harness uses.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

import boto3
from botocore.exceptions import ClientError

from demo_harness.config import (
    APPROVAL_GATE_FUNCTION,
    AWS_REGION,
    MASTER_STORE_TABLE,
    REQUISITIONS_TABLE,
    TENANT_ID,
)

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import DynamoDBServiceResource

logger = logging.getLogger("demo_harness.health")

_INFORMATIVE_SOURCES = {"auto_priced", "supplier_response_seed"}

_lambda_client = boto3.client("lambda", region_name=AWS_REGION)
_ddb_resource = cast("DynamoDBServiceResource", boto3.resource("dynamodb", region_name=AWS_REGION))
_sfn_client = boto3.client("stepfunctions", region_name=AWS_REGION)
_state_machine_arn: str | None = None
_state_machine_arn_resolved = False


def resolve_state_machine_arn() -> str | None:
    """The buyer-team state machine ARN doesn't change at runtime — cache it
    so /traces doesn't call list_state_machines on every request."""
    global _state_machine_arn, _state_machine_arn_resolved
    if not _state_machine_arn_resolved:
        machines = _sfn_client.list_state_machines()["stateMachines"]
        _state_machine_arn = next(
            (m["stateMachineArn"] for m in machines if "buyer-team" in m["name"]), None
        )
        _state_machine_arn_resolved = True
    return _state_machine_arn


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

    checks["step_functions"] = _check_step_functions()

    pricing_info = _get_pricing_mode()

    healthy = all(v == "ok" for v in checks.values())
    if healthy:
        logger.info("Buyer Team health check OK: %s", checks)
    else:
        logger.warning("Buyer Team health check FAILED: %s", checks)
    return {"healthy": healthy, "checks": checks, **pricing_info}


def _check_step_functions() -> str:
    arn = resolve_state_machine_arn()
    if arn is None:
        return "error: state machine not found"
    try:
        status = _sfn_client.describe_state_machine(stateMachineArn=arn)["status"]
        return "ok" if status == "ACTIVE" else f"error: status={status}"
    except ClientError as e:
        return f"error: {e.response['Error']['Code']}"


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
