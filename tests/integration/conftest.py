"""Shared fixtures and constants for the AWS integration suite (us-east-1).

These tests run against the live dev environment. They are skipped entirely
unless RUN_INTEGRATION=1 (enforced by the parent tests/conftest.py) and are
further skipped if AWS credentials are not resolvable.
"""

from __future__ import annotations

from functools import cache

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import BotoCoreError, NoCredentialsError

REGION = "us-east-1"
ACCOUNT_ID = "234876310489"
TENANT = "6eb4ebaf-804e-5837-ae26-f665a76b58dd"

EXPECTED_RUNTIMES = {
    "dev_kraljic_classifier",
    "dev_spot_bidding",
    "dev_leverage_auction",
    "dev_bottleneck_negotiation",
    "dev_strategic_partnership",
    "dev_bid_evaluation",
    "dev_award_comms",
    "dev_skill_runtime",
}

# Domain/system table key schema — see memory project-dynamodb-schema.
TABLE_KEY_SCHEMA = {
    "dev-categories": ("tenant_id", "category_id"),
    "dev-suppliers": ("tenant_id", "supplier_id"),
    "dev-negotiations": ("tenant_id", "negotiation_id"),
    "dev-bids": ("tenant_id", "bid_id"),
    "dev-awards": ("tenant_id", "award_id"),
    "dev-items": ("tenant_id", "item_id"),
    "dev-category-suppliers": ("tenant_id", "category_id"),
    "dev-dataset-status": ("tenant_id", "sk"),
    "dev-tenants": ("pk", "sk"),
}

_CFG = Config(connect_timeout=10, read_timeout=30, retries={"mode": "adaptive", "max_attempts": 4})
# Data-plane agent invocations run an LLM tool loop (+ structured_output) that can
# take a few minutes; the 30s read timeout above is only right for control-plane/DDB.
_INVOKE_CFG = Config(
    connect_timeout=10, read_timeout=300, retries={"mode": "adaptive", "max_attempts": 2}
)


@cache
def _session() -> boto3.Session:
    return boto3.Session(region_name=REGION)


@cache
def _ddb():
    return _session().resource("dynamodb", config=_CFG)


@pytest.fixture(scope="session", autouse=True)
def _require_credentials():
    """Skip the whole suite cleanly if no AWS credentials are available."""
    try:
        ident = _session().client("sts", config=_CFG).get_caller_identity()
    except (NoCredentialsError, BotoCoreError) as exc:
        pytest.skip(f"AWS credentials not available: {exc}")
    if ident["Account"] != ACCOUNT_ID:
        pytest.skip(f"Wrong AWS account {ident['Account']}, expected {ACCOUNT_ID}")


@pytest.fixture
def sts():
    return _session().client("sts", config=_CFG)


@pytest.fixture
def agentcore_control():
    return _session().client("bedrock-agentcore-control", config=_CFG)


@pytest.fixture
def agentcore():
    return _session().client("bedrock-agentcore", config=_INVOKE_CFG)


@pytest.fixture
def ddb():
    return _ddb()


@pytest.fixture
def table(ddb):
    return ddb.Table
