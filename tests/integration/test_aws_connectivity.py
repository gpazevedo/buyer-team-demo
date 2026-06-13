"""Smoke: AWS credentials resolve and point at the expected dev account."""
from .conftest import ACCOUNT_ID, REGION


def test_caller_identity_is_expected_account(sts):
    ident = sts.get_caller_identity()
    assert ident["Account"] == ACCOUNT_ID


def test_region_is_us_east_1(sts):
    assert sts.meta.region_name == REGION
