"""Blue Jets tenant seed — idempotent via uuid5 inline idiom (PRD-020 §4).

Creates: tenant, 4 categories (one per Kraljic quadrant), 4 items, 8 suppliers
(3+ candidates per quadrant for a real multi-bid negotiation), and
category-supplier associations. Safe to run repeatedly.

Usage:
  uv run python -m demo_harness.seed [--env dev] [--region us-east-1]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from typing import TYPE_CHECKING, cast
from uuid import NAMESPACE_DNS, uuid5

import boto3
from test_tenant_app.clients.ddb import to_decimal

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import DynamoDBServiceResource

logger = logging.getLogger("demo_harness.seed")

ENV = os.getenv("ENV", "dev")
REGION = os.getenv("AWS_REGION", "us-east-1")

TENANT_ID = str(uuid5(NAMESPACE_DNS, "root:tenant:blue-jets"))

# ── Category definitions (PRD-020 §4.2) ──────────────────────────

CATEGORIES = {
    "NON_CRITICAL": {
        "name": "Cabin & Lavatory Consumables (ATA 25/38)",
        "profit_impact": 0.30,
        "supply_risk": 0.20,
        "quadrant": "NON_CRITICAL",
    },
    "LEVERAGE": {
        "name": "Wheels, Tires & Common Fasteners (ATA 32/20)",
        "profit_impact": 0.70,
        "supply_risk": 0.25,
        "quadrant": "LEVERAGE",
    },
    "BOTTLENECK": {
        "name": "Avionics LRUs — VHF/Weather Radar (ATA 23/34)",
        "profit_impact": 0.35,
        "supply_risk": 0.75,
        "quadrant": "BOTTLENECK",
    },
    "STRATEGIC": {
        "name": "Engine LLPs & APU (ATA 72/49)",
        "profit_impact": 0.80,
        "supply_risk": 0.80,
        "quadrant": "STRATEGIC",
    },
}

# ── Item definitions (PRD-020 §4.4) ──────────────────────────────

ITEMS = {
    "NON_CRITICAL": {
        "sku": "BJ-25-LAVKIT",
        "name": "Lavatory service consumable kit",
        "ata": "38-10",
        "estimated_unit_price": 180,
        "lead_time_days": 7,
    },
    "LEVERAGE": {
        "sku": "BJ-32-MWTIRE",
        "name": "Main wheel tire, radial",
        "ata": "32-45",
        "estimated_unit_price": 2400,
        "lead_time_days": 14,
    },
    "BOTTLENECK": {
        "sku": "BJ-23-VHFXCVR",
        "name": "VHF COMM transceiver",
        "ata": "23-12",
        "estimated_unit_price": 11800,
        "lead_time_days": 30,
    },
    "STRATEGIC": {
        "sku": "BJ-72-HPTBLD",
        "name": "HPT stage-1 blade set (LLP)",
        "ata": "72-53",
        "estimated_unit_price": 96000,
        "lead_time_days": 90,
    },
}

# ── Supplier definitions (PRD-020 §4.5) ──────────────────────────

SUPPLIERS = [
    {
        "name": "AeroStock Intl",
        "cage": "7KX44",
        "quadrants": ["NON_CRITICAL", "LEVERAGE"],
        "performance_score": 0.82,
        "quality_score": 0.78,
        "esg_score": 0.72,
        "risk_rating": "LOW",
        "on_time_delivery_rate": 0.94,
    },
    {
        "name": "SkyParts Distribution",
        "cage": "4LM09",
        "quadrants": ["NON_CRITICAL", "LEVERAGE"],
        "performance_score": 0.74,
        "quality_score": 0.71,
        "esg_score": 0.65,
        "risk_rating": "MEDIUM",
        "on_time_delivery_rate": 0.88,
    },
    {
        "name": "TurbineTech OEM",
        "cage": "13499",
        "quadrants": ["STRATEGIC", "BOTTLENECK"],
        "performance_score": 0.88,
        "quality_score": 0.90,
        "esg_score": 0.85,
        "risk_rating": "LOW",
        "on_time_delivery_rate": 0.96,
    },
    {
        "name": "Avionics Prime",
        "cage": "9AB12",
        "quadrants": ["BOTTLENECK"],
        "performance_score": 0.79,
        "quality_score": 0.83,
        "esg_score": 0.78,
        "risk_rating": "MEDIUM",
        "on_time_delivery_rate": 0.91,
    },
    {
        "name": "GlobalWheel Co",
        "cage": "6PT31",
        "quadrants": ["LEVERAGE"],
        "performance_score": 0.71,
        "quality_score": 0.69,
        "esg_score": 0.63,
        "risk_rating": "MEDIUM",
        "on_time_delivery_rate": 0.85,
    },
    {
        "name": "CabinSource Aero",
        "cage": "2DF77",
        "quadrants": ["NON_CRITICAL"],
        "performance_score": 0.76,
        "quality_score": 0.73,
        "esg_score": 0.68,
        "risk_rating": "LOW",
        "on_time_delivery_rate": 0.90,
    },
    {
        "name": "Continental Engine Parts",
        "cage": "88GHT",
        "quadrants": ["STRATEGIC", "BOTTLENECK"],
        "performance_score": 0.84,
        "quality_score": 0.86,
        "esg_score": 0.74,
        "risk_rating": "LOW",
        "on_time_delivery_rate": 0.93,
    },
    {
        "name": "Apex Rotables",
        "cage": "5QW20",
        "quadrants": ["STRATEGIC"],
        "performance_score": 0.68,
        "quality_score": 0.66,
        "esg_score": 0.58,
        "risk_rating": "HIGH",
        "on_time_delivery_rate": 0.81,
    },
]


def _category_id(quadrant: str) -> str:
    return str(uuid5(NAMESPACE_DNS, f"root:category:blue-jets:{quadrant.lower()}"))


def _item_id(sku: str) -> str:
    return str(uuid5(NAMESPACE_DNS, f"root:item:blue-jets:{sku}"))


def _supplier_id(name: str) -> str:
    return str(uuid5(NAMESPACE_DNS, f"root:supplier:blue-jets:{name}"))


# ── Seed functions ────────────────────────────────────────────────


def seed_tenant(ddb, env: str) -> None:
    table = ddb.Table(f"{env}-tenants")
    table.put_item(
        Item=to_decimal(
            {
                "pk": TENANT_ID,
                "sk": "metadata",
                "tenant_id": TENANT_ID,
                "display_name": "Blue Jets",
                "status": "ACTIVE",
                "default_currency": "USD",
            }
        )
    )
    print(f"[OK] {env}-tenants: blue-jets={TENANT_ID}")


def seed_categories(ddb, env: str) -> dict[str, str]:
    """Seed 4 categories, one per quadrant. Returns {quadrant: category_id}."""
    table = ddb.Table(f"{env}-categories")
    cat_ids: dict[str, str] = {}
    for quadrant, cat in CATEGORIES.items():
        cid = _category_id(quadrant)
        cat_ids[quadrant] = cid
        table.put_item(
            Item=to_decimal(
                {
                    "tenant_id": TENANT_ID,
                    "category_id": cid,
                    "name": cat["name"],
                    "profit_impact": cat["profit_impact"],
                    "supply_risk": cat["supply_risk"],
                    "kraljic_quadrant": cat["quadrant"],
                    "currency": "USD",
                }
            )
        )
        print(f"[OK] {env}-categories: {cat['name']} ({quadrant}) = {cid}")
    return cat_ids


def seed_items(ddb, env: str, cat_ids: dict[str, str]) -> dict[str, str]:
    """Seed 4 items, one per category. Returns {quadrant: item_id}."""
    table = ddb.Table(f"{env}-items")
    item_ids: dict[str, str] = {}
    for quadrant, item in ITEMS.items():
        iid = _item_id(item["sku"])
        item_ids[quadrant] = iid
        table.put_item(
            Item=to_decimal(
                {
                    "tenant_id": TENANT_ID,
                    "item_id": iid,
                    "category_id": cat_ids[quadrant],
                    "name": item["name"],
                    "sku": item["sku"],
                    "estimated_unit_price": item["estimated_unit_price"],
                    "lead_time_days": item["lead_time_days"],
                    "currency": "USD",
                }
            )
        )
        print(f"[OK] {env}-items: {item['sku']} ({item['name']}) = {iid}")
    return item_ids


def seed_suppliers(ddb, env: str, cat_ids: dict[str, str]) -> dict[str, str]:
    """Seed suppliers (3+ per quadrant) + category-supplier associations. Returns {name: supplier_id}."""
    sup_table = ddb.Table(f"{env}-suppliers")
    cs_table = ddb.Table(f"{env}-category-suppliers")
    sup_ids: dict[str, str] = {}
    by_quadrant: dict[str, list[str]] = {}
    for sup in SUPPLIERS:
        sid = _supplier_id(sup["name"])
        sup_ids[sup["name"]] = sid
        sup_table.put_item(
            Item=to_decimal(
                {
                    "tenant_id": TENANT_ID,
                    "supplier_id": sid,
                    "name": sup["name"],
                    "status": "ACTIVE",
                    "cage_code": sup["cage"],
                    "performance_score": sup["performance_score"],
                    "quality_score": sup["quality_score"],
                    "esg_score": sup["esg_score"],
                    "risk_rating": sup["risk_rating"],
                    "on_time_delivery_rate": sup["on_time_delivery_rate"],
                }
            )
        )
        print(f"[OK] {env}-suppliers: {sup['name']} (CAGE {sup['cage']}) = {sid}")
        for quadrant in sup["quadrants"]:
            by_quadrant.setdefault(quadrant, []).append(sid)

    # category-suppliers PK is (tenant_id, category_id) only — one row per
    # category, so every candidate for that category goes on a single
    # `supplier_ids` list rather than one row per supplier (which would just
    # overwrite down to the last one). Matches the impl orchestrator's
    # `_candidate_supplier_ids`, which already reads this list attribute.
    for quadrant, supplier_ids in by_quadrant.items():
        cs_table.put_item(
            Item=to_decimal(
                {
                    "tenant_id": TENANT_ID,
                    "category_id": cat_ids[quadrant],
                    "supplier_ids": supplier_ids,
                }
            )
        )
        print(f"[OK] {env}-category-suppliers: {quadrant} -> {len(supplier_ids)} suppliers")
    return sup_ids


def seed_governance_override(ddb, env: str) -> None:
    """Blue Jets governance override: a $5k auto-approval ceiling (below the
    shared `governance/default` profile's $10k) so the LEVERAGE demo quadrant
    pauses for HITL once qty>=3 pushes the award over $5k."""
    table = ddb.Table(f"{env}-system-config")
    override = {
        "approval_thresholds": {
            "auto_approve_below_usd": 5000,
            "escalation_timeout_hours": 48,
            "require_second_approver_above_usd": 50000,
            "negotiation_quality_composite_minimum": 0.67,
        },
    }
    table.put_item(
        Item={
            "config_group": "governance",
            "config_key": f"tenant#{TENANT_ID}",
            "config_json": json.dumps(override),
            "version": "1.0",
        }
    )
    print(
        f"[OK] {env}-system-config: governance tenant#{TENANT_ID} override (auto_approve_below_usd=5000)"
    )


def seed(env: str = ENV, region: str = REGION) -> dict:
    """Run the full Blue Jets seed. Idempotent — safe to re-run."""
    logger.info("seeding Blue Jets tenant env=%s region=%s", env, region)
    ddb = boto3.resource("dynamodb", region_name=region)
    seed_tenant(ddb, env)
    cat_ids = seed_categories(ddb, env)
    item_ids = seed_items(ddb, env, cat_ids)
    sup_ids = seed_suppliers(ddb, env, cat_ids)
    seed_governance_override(ddb, env)
    logger.info(
        "seed complete tenant_id=%s categories=%d items=%d suppliers=%d",
        TENANT_ID,
        len(cat_ids),
        len(item_ids),
        len(sup_ids),
    )
    return {
        "tenant_id": TENANT_ID,
        "category_ids": cat_ids,
        "item_ids": item_ids,
        "supplier_ids": sup_ids,
    }


def seed_status(env: str = ENV, region: str = REGION) -> dict:
    """Read-only check: what Blue Jets entities exist?"""
    ddb = cast("DynamoDBServiceResource", boto3.resource("dynamodb", region_name=region))

    def _count(table_suffix: str) -> int:
        try:
            table = ddb.Table(f"{env}-{table_suffix}")
            # tables keyed by tenant_id use scan; others (tenants, requisitions) vary
            if table_suffix in ("tenants",):
                resp = table.get_item(Key={"pk": TENANT_ID, "sk": "metadata"})
                return 1 if resp.get("Item") else 0
            if table_suffix == "requisitions":
                from boto3.dynamodb.conditions import Attr

                resp = table.scan(FilterExpression=Attr("pk").begins_with(f"{TENANT_ID}#"))
                return len(resp.get("Items", []))
            # Standard domain tables keyed by tenant_id
            from boto3.dynamodb.conditions import Key

            resp = table.query(
                KeyConditionExpression=Key("tenant_id").eq(TENANT_ID),
                Select="COUNT",
            )
            return resp.get("Count", 0)
        except Exception:
            return -1

    return {
        "tenant_id": TENANT_ID,
        "tenant": _count("tenants"),
        "categories": _count("categories"),
        "items": _count("items"),
        "suppliers": _count("suppliers"),
        "category_suppliers": _count("category-suppliers"),
        "requisitions": _count("requisitions"),
    }


# ── CLI ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed Blue Jets tenant")
    parser.add_argument("--env", default=ENV, help=f"DynamoDB env prefix (default: {ENV})")
    parser.add_argument("--region", default=REGION, help=f"AWS region (default: {REGION})")
    parser.add_argument("--status", action="store_true", help="Print seed status and exit")
    args = parser.parse_args()

    os.environ["SKILL_MODE"] = "live"

    if args.status:
        import json as _json

        print(_json.dumps(seed_status(args.env, args.region), indent=2))
    else:
        result = seed(args.env, args.region)
        print(f"\nSeed complete. tenant_id={result['tenant_id']}")
