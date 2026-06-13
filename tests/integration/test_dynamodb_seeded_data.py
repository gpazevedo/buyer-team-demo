"""DynamoDB checks: domain tables exist with the right key schema and the
test tenant's seeded data is present and readable.

A read failure here (e.g. KMSInvalidStateException) surfaces an environment
problem — encryption key inaccessible, data wiped — rather than a code bug.
"""
from boto3.dynamodb.conditions import Key

from .conftest import TABLE_KEY_SCHEMA, TENANT

import pytest


@pytest.mark.parametrize("name,schema", sorted(TABLE_KEY_SCHEMA.items()))
def test_table_key_schema(ddb, name, schema):
    desc = ddb.meta.client.describe_table(TableName=name)["Table"]
    keys = {k["KeyType"]: k["AttributeName"] for k in desc["KeySchema"]}
    assert (keys.get("HASH"), keys.get("RANGE")) == schema


def test_test_tenant_categories_seeded(ddb):
    """Test tenant carries its seeded Kraljic categories (20 at load time)."""
    resp = ddb.Table("dev-categories").query(
        KeyConditionExpression=Key("tenant_id").eq(TENANT),
        Select="COUNT",
    )
    assert resp["Count"] >= 20


def test_test_tenant_suppliers_seeded(ddb):
    resp = ddb.Table("dev-suppliers").query(
        KeyConditionExpression=Key("tenant_id").eq(TENANT),
        Select="COUNT",
    )
    assert resp["Count"] >= 1


def test_test_tenant_record_exists(ddb):
    item = ddb.Table("dev-tenants").get_item(
        Key={"pk": TENANT, "sk": "metadata"}
    ).get("Item")
    assert item is not None
