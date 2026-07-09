"""Reset Blue Jets demo runtime data — deletes PR→PO output, keeps seed data intact.

Safe to re-run. Deletes from: negotiations, bids, awards, requisitions, orders,
communications, agent-session-cache, test-tenant-orders, master-purchase-requisitions.

KEEPS: tenants, categories, items, suppliers, category-suppliers.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from demo_harness.config import TENANT_ID

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient

logger = logging.getLogger("demo_harness.reset_demo")

TENANT_PK_PREFIX = f"{TENANT_ID}#"


# ── helpers ────────────────────────────────────────────────────────


def _query_keys(
    client: DynamoDBClient,
    table: str,
    key_names: list[str],
    **kwargs,
) -> list[dict]:
    """Query a table and return all items' key dicts (for batch delete)."""
    keys: list[dict] = []
    paginator = client.get_paginator("query")
    for page in paginator.paginate(TableName=table, **kwargs):
        for item in page.get("Items", []):
            keys.append({k: item[k] for k in key_names})
    return keys


def _scan_keys(
    client: DynamoDBClient,
    table: str,
    key_names: list[str],
    **kwargs,
) -> list[dict]:
    """Scan a table and return all items' key dicts (for batch delete)."""
    keys: list[dict] = []
    paginator = client.get_paginator("scan")
    for page in paginator.paginate(TableName=table, **kwargs):
        for item in page.get("Items", []):
            keys.append({k: item[k] for k in key_names})
    return keys


def _delete_batch(client: DynamoDBClient, table: str, keys: list[dict]) -> int:
    """Batch-delete keys in chunks of 25. Returns count deleted."""
    if not keys:
        return 0
    deleted = 0
    for i in range(0, len(keys), 25):
        chunk = keys[i : i + 25]
        client.batch_write_item(
            RequestItems={table: [{"DeleteRequest": {"Key": k}} for k in chunk]}
        )
        deleted += len(chunk)
    return deleted


# ── table-specific resets ──────────────────────────────────────────


def _delete_tenant_id_hash(client: DynamoDBClient, table: str, sort_key: str) -> int:
    """Delete items where tenant_id (HASH) == TENANT_ID."""
    keys = _query_keys(
        client,
        table,
        key_names=["tenant_id", sort_key],
        KeyConditionExpression="tenant_id = :tid",
        ExpressionAttributeValues={":tid": {"S": TENANT_ID}},
    )
    return _delete_batch(client, table, keys)


def _delete_tenant_pk_prefix(client: DynamoDBClient, table: str) -> int:
    """Delete items where pk begins with TENANT_ID#."""
    keys = _scan_keys(
        client,
        table,
        key_names=["pk", "sk"],
        FilterExpression="begins_with(pk, :prefix)",
        ExpressionAttributeValues={":prefix": {"S": TENANT_PK_PREFIX}},
    )
    return _delete_batch(client, table, keys)


def _clear_table(client: DynamoDBClient, table: str) -> int:
    """Delete ALL items from a table (use only for cache/temp tables)."""
    desc = client.describe_table(TableName=table)
    key_names = [k["AttributeName"] for k in desc["Table"].get("KeySchema", [])]
    keys = _scan_keys(client, table, key_names)
    return _delete_batch(client, table, keys)


# ── main ───────────────────────────────────────────────────────────


def _get_negotiation_ids(client: DynamoDBClient, env: str) -> set[str]:
    """Capture Blue Jets negotiation IDs BEFORE deleting them."""
    neg_keys = _query_keys(
        client,
        f"{env}-negotiations",
        key_names=["negotiation_id"],
        KeyConditionExpression="tenant_id = :tid",
        ExpressionAttributeValues={":tid": {"S": TENANT_ID}},
    )
    return {n["negotiation_id"]["S"] for n in neg_keys if n.get("negotiation_id", {}).get("S")}


def _delete_communications_by_neg_ids(client: DynamoDBClient, table: str, neg_ids: set[str]) -> int:
    """Delete communications matching Blue Jets negotiation IDs or tenant_id prefix."""
    all_keys = _scan_keys(client, table, key_names=["pk", "sk"])
    to_delete = []
    for k in all_keys:
        pk = k["pk"]["S"]
        if pk in neg_ids or pk.startswith(TENANT_PK_PREFIX) or pk.startswith(TENANT_ID):
            to_delete.append(k)
    return _delete_batch(client, table, to_delete)


def reset(env: str = "dev") -> dict[str, int]:
    """Delete all Blue Jets runtime data. Returns {table: count_deleted}."""
    import boto3

    client = cast("DynamoDBClient", boto3.client("dynamodb", region_name="us-east-1"))
    counts: dict[str, int] = {}

    # Capture Blue Jets negotiation IDs BEFORE deleting negotiations
    blue_jets_neg_ids = _get_negotiation_ids(client, env)
    print(f"[INFO] Blue Jets negotiation IDs: {len(blue_jets_neg_ids)} found")

    # Tables keyed by tenant_id (HASH)
    counts["negotiations"] = _delete_tenant_id_hash(client, f"{env}-negotiations", "negotiation_id")
    print(f"[OK] {env}-negotiations: {counts['negotiations']} items deleted")

    counts["bids"] = _delete_tenant_id_hash(client, f"{env}-bids", "bid_id")
    print(f"[OK] {env}-bids: {counts['bids']} items deleted")

    counts["awards"] = _delete_tenant_id_hash(client, f"{env}-awards", "award_id")
    print(f"[OK] {env}-awards: {counts['awards']} items deleted")

    counts["master_requisitions"] = _delete_tenant_id_hash(
        client, f"{env}-test-tenant-master-purchase-requisitions", "requisition_id"
    )
    print(
        f"[OK] {env}-test-tenant-master-purchase-requisitions: {counts['master_requisitions']} items deleted"
    )

    # Tables where pk = {tenant_id}#{id}
    counts["requisitions"] = _delete_tenant_pk_prefix(client, f"{env}-requisitions")
    print(f"[OK] {env}-requisitions: {counts['requisitions']} items deleted")

    counts["orders"] = _delete_tenant_pk_prefix(client, f"{env}-orders")
    print(f"[OK] {env}-orders: {counts['orders']} items deleted")

    counts["test_tenant_orders"] = _delete_tenant_pk_prefix(client, f"{env}-test-tenant-orders")
    print(f"[OK] {env}-test-tenant-orders: {counts['test_tenant_orders']} items deleted")

    # Communications — match by negotiation_id or tenant_id prefix
    counts["communications"] = _delete_communications_by_neg_ids(
        client, f"{env}-communications", blue_jets_neg_ids
    )
    print(f"[OK] {env}-communications: {counts['communications']} items deleted")

    # agent-session-cache — clear entirely (transient cache, no seed data)
    counts["agent_session_cache"] = _clear_table(client, f"{env}-agent-session-cache")
    print(f"[OK] {env}-agent-session-cache: {counts['agent_session_cache']} items deleted")

    return counts


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Reset Blue Jets demo runtime data")
    parser.add_argument("--env", default="dev", help="Env prefix (default: dev)")
    args = parser.parse_args()
    reset(args.env)
