"""reset_demo helpers — pagination flattening, batched deletes, and the
communications matching rule (by negotiation_id or by tenant pk prefix).
Exercised against a fake low-level DynamoDB client, no AWS involved.
"""

from demo_harness.config import TENANT_ID
from demo_harness.reset_demo import (
    TENANT_PK_PREFIX,
    _delete_batch,
    _delete_communications_by_neg_ids,
    _query_keys,
    _scan_keys,
)


class _FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kwargs):
        return self._pages


class _FakeClient:
    def __init__(self, pages):
        self._pages = pages
        self.batch_write_calls: list[dict] = []

    def get_paginator(self, _operation_name):
        return _FakePaginator(self._pages)

    def batch_write_item(self, RequestItems):
        self.batch_write_calls.append(RequestItems)


def test_query_keys_flattens_pages_and_extracts_key_names():
    pages = [
        {"Items": [{"tenant_id": {"S": "t1"}, "negotiation_id": {"S": "n1"}, "extra": {"S": "x"}}]},
        {"Items": [{"tenant_id": {"S": "t1"}, "negotiation_id": {"S": "n2"}}]},
    ]
    client = _FakeClient(pages)

    keys = _query_keys(client, "dev-negotiations", key_names=["tenant_id", "negotiation_id"])

    assert keys == [
        {"tenant_id": {"S": "t1"}, "negotiation_id": {"S": "n1"}},
        {"tenant_id": {"S": "t1"}, "negotiation_id": {"S": "n2"}},
    ]


def test_query_keys_empty_pages_returns_empty_list():
    client = _FakeClient([{"Items": []}])
    assert _query_keys(client, "dev-negotiations", key_names=["negotiation_id"]) == []


def test_scan_keys_flattens_pages():
    pages = [{"Items": [{"pk": {"S": "p1"}, "sk": {"S": "s1"}}]}]
    client = _FakeClient(pages)

    keys = _scan_keys(client, "dev-communications", key_names=["pk", "sk"])

    assert keys == [{"pk": {"S": "p1"}, "sk": {"S": "s1"}}]


def test_delete_batch_empty_list_is_noop():
    client = _FakeClient([])
    assert _delete_batch(client, "dev-bids", []) == 0
    assert client.batch_write_calls == []


def test_delete_batch_single_chunk_under_25():
    client = _FakeClient([])
    keys = [{"bid_id": {"S": str(i)}} for i in range(10)]

    deleted = _delete_batch(client, "dev-bids", keys)

    assert deleted == 10
    assert len(client.batch_write_calls) == 1
    assert len(client.batch_write_calls[0]["dev-bids"]) == 10


def test_delete_batch_splits_into_chunks_of_25():
    client = _FakeClient([])
    keys = [{"bid_id": {"S": str(i)}} for i in range(60)]

    deleted = _delete_batch(client, "dev-bids", keys)

    assert deleted == 60
    assert len(client.batch_write_calls) == 3  # 25 + 25 + 10
    assert [len(c["dev-bids"]) for c in client.batch_write_calls] == [25, 25, 10]


def test_delete_communications_matches_by_negotiation_id():
    pages = [{"Items": [{"pk": {"S": "neg-abc"}, "sk": {"S": "metadata"}}]}]
    client = _FakeClient(pages)

    deleted = _delete_communications_by_neg_ids(client, "dev-communications", {"neg-abc"})

    assert deleted == 1


def test_delete_communications_matches_by_tenant_prefix():
    pages = [{"Items": [{"pk": {"S": f"{TENANT_ID}#comm-1"}, "sk": {"S": "metadata"}}]}]
    client = _FakeClient(pages)

    deleted = _delete_communications_by_neg_ids(client, "dev-communications", set())

    assert deleted == 1


def test_delete_communications_ignores_unrelated_rows():
    pages = [{"Items": [{"pk": {"S": "some-other-tenant#comm-1"}, "sk": {"S": "metadata"}}]}]
    client = _FakeClient(pages)

    deleted = _delete_communications_by_neg_ids(client, "dev-communications", {"neg-abc"})

    assert deleted == 0


def test_tenant_pk_prefix_matches_config():
    assert TENANT_PK_PREFIX == f"{TENANT_ID}#"
