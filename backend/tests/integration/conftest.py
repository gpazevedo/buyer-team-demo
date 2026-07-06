"""Shared fixtures for the AWS integration suite (us-east-1).

Runs against the live dev environment. Skipped entirely unless
RUN_INTEGRATION=1 (enforced by the parent tests/conftest.py) and further
skipped if AWS credentials are not resolvable — mirrors
test_tenant_app/backend/tests/integration/conftest.py.
"""

from __future__ import annotations

from functools import cache

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import BotoCoreError, NoCredentialsError

REGION = "us-east-1"
ACCOUNT_ID = "234876310489"

_CFG = Config(connect_timeout=10, read_timeout=30, retries={"mode": "adaptive", "max_attempts": 4})


@cache
def _session() -> boto3.Session:
    return boto3.Session(region_name=REGION)


@pytest.fixture(scope="session", autouse=True)
def _require_credentials():
    """Skip the whole suite cleanly if no AWS credentials are available."""
    try:
        ident = _session().client("sts", config=_CFG).get_caller_identity()
    except (NoCredentialsError, BotoCoreError) as exc:
        pytest.skip(f"AWS credentials not available: {exc}")
    if ident["Account"] != ACCOUNT_ID:
        pytest.skip(f"Wrong AWS account {ident['Account']}, expected {ACCOUNT_ID}")
