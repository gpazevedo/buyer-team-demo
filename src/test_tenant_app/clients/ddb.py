"""Shared DynamoDB access for live mode (SKILL_MODE=live).

Live mode talks to the real `{ENV}-*` tables directly, bypassing the not-yet-
deployed MCP/orchestrator layer. Reads/writes go through one cached resource.
"""
from __future__ import annotations

import os
from decimal import Decimal
from functools import cache

import boto3

ENV = os.getenv("ENV", "dev")
REGION = os.getenv("AWS_REGION", "us-east-1")
DYNAMODB_ENDPOINT = os.getenv("DYNAMODB_ENDPOINT", "")


@cache
def _resource():
    kwargs = {"region_name": REGION}
    if DYNAMODB_ENDPOINT:
        kwargs["endpoint_url"] = DYNAMODB_ENDPOINT
    return boto3.resource("dynamodb", **kwargs)


def table(name: str):
    """Return the boto3 Table for `{ENV}-{name}`."""
    return _resource().Table(f"{ENV}-{name}")


def to_decimal(obj):
    """Recursively convert floats to Decimal for DynamoDB writes."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, list):
        return [to_decimal(v) for v in obj]
    if isinstance(obj, dict):
        return {k: to_decimal(v) for k, v in obj.items()}
    return obj


def to_native(obj):
    """Recursively convert DynamoDB Decimals back to int/float for Pydantic/JSON."""
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    if isinstance(obj, list):
        return [to_native(v) for v in obj]
    if isinstance(obj, dict):
        return {k: to_native(v) for k, v in obj.items()}
    return obj
