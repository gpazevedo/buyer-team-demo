"""Is Buyer Team actually reachable? Checks the AWS resources this harness
depends on directly: the Node 6 approval-gate Lambda (S4), the master-store /
requisitions DynamoDB tables (S1), the buyer-team Step Functions state machine,
and the Bedrock AgentCore runtimes the agent tier runs on. Not a full platform
health check — just the seams this harness uses.

Each check is reported under its own key so the UI can name the AWS service it
stands for, rather than collapsing everything into one reachable/unreachable dot.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

import boto3
from botocore.exceptions import ClientError

from demo_harness.config import (
    APPROVAL_GATE_FUNCTION,
    AWS_REGION,
    ENV,
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
_agentcore_client = boto3.client("bedrock-agentcore-control", region_name=AWS_REGION)
_sts_client = boto3.client("sts", region_name=AWS_REGION)
_cloudwatch_client = boto3.client("cloudwatch", region_name=AWS_REGION)
_state_machine_arn: str | None = None
_state_machine_arn_resolved = False
_identity: dict[str, str] | None = None


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
    checks["agentcore_runtimes"] = _check_agentcore()

    pricing_info = _get_pricing_mode()

    healthy = all(v == "ok" for v in checks.values())
    if healthy:
        logger.info("Buyer Team health check OK: %s", checks)
    else:
        logger.warning("Buyer Team health check FAILED: %s", checks)
    return {"healthy": healthy, "checks": checks, "identity": aws_identity(), **pricing_info}


def aws_identity() -> dict[str, str]:
    """Which AWS account/region/env this harness is actually pointed at — shown
    in the header so it's unambiguous that the demo runs on real AWS. The account
    doesn't change at runtime, so resolve it once."""
    global _identity
    if _identity is None:
        try:
            account = _sts_client.get_caller_identity()["Account"]
        except ClientError:
            logger.warning("could not resolve AWS account id", exc_info=True)
            account = "unknown"
        _identity = {"account": account, "region": AWS_REGION, "env": ENV}
    return _identity


def _check_agentcore() -> str:
    """Bedrock AgentCore control plane + runtime readiness. This is a control-plane
    call, so it stays 'ok' even when NAT is down — the agents' *invocation* path is
    what VPC/NAT gates, and that's what the pricing_mode badge reports."""
    try:
        runtimes = _agentcore_client.list_agent_runtimes()["agentRuntimes"]
    except ClientError as e:
        return f"error: {e.response['Error']['Code']}"
    if not runtimes:
        return "error: no agent runtimes"
    ready = sum(1 for r in runtimes if r.get("status") == "READY")
    return "ok" if ready == len(runtimes) else f"degraded: {ready}/{len(runtimes)} READY"


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
