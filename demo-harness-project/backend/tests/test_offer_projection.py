"""poll_once — merges DynamoDB reads into the in-memory projection and fires
SSE events on change. All DynamoDB access is monkeypatched at the module's
own read functions (_query_*, _cached_supplier_name_map, dynamo_client) so
these run with no AWS calls.

Each published-event dedup set (_published_statuses, _published_offers, ...)
is module-level and keyed by negotiation_id, so every test uses its own
negotiation_id and an autouse fixture clears all in-memory state up front.
"""

import itertools

import demo_harness.offer_projection as op
import pytest

_ids = itertools.count()


def _fresh_negotiation_id() -> str:
    return f"neg-test-{next(_ids)}"


@pytest.fixture(autouse=True)
def _clear_projection_state():
    op._state.clear()
    op._subscribers.clear()
    op._global_subscribers.clear()
    op._published_statuses.clear()
    op._published_awards.clear()
    op._published_orders.clear()
    op._published_offers.clear()
    op._published_invites.clear()
    op._published_feedback.clear()
    op._published_classifications.clear()
    op._synthetic_order_ids.clear()
    op._synthetic_neg_ids.clear()
    op._correlation_ids.clear()
    yield


def _stub_reads(
    monkeypatch,
    neg=None,
    bids=None,
    invitations=None,
    feedback=None,
    names=None,
    awards=None,
    orders=None,
):
    monkeypatch.setattr(op, "_query_negotiation", lambda _nid: neg)
    monkeypatch.setattr(op, "_query_bids", lambda _nid: bids or [])
    monkeypatch.setattr(op, "_query_communications", lambda _nid: invitations or [])
    monkeypatch.setattr(op, "_query_feedback", lambda _nid: feedback or [])
    monkeypatch.setattr(op, "_cached_supplier_name_map", lambda _tid: names or {})
    monkeypatch.setattr(op.dynamo_client, "get_awards", lambda _tid, _rid: awards or [])
    monkeypatch.setattr(op.dynamo_client, "get_orders", lambda _tid: orders or [])


async def _drain(queue) -> list[dict]:
    import json

    events = []
    while not queue.empty():
        events.append(json.loads(queue.get_nowait()))
    return events


@pytest.mark.asyncio
async def test_returns_none_when_negotiation_not_found_and_untracked(monkeypatch):
    nid = _fresh_negotiation_id()
    _stub_reads(monkeypatch, neg=None)

    assert await op.poll_once(nid) is None


@pytest.mark.asyncio
async def test_builds_snapshot_with_supplier_name_backfilled(monkeypatch):
    nid = _fresh_negotiation_id()
    _stub_reads(
        monkeypatch,
        neg={
            "requisition_id": "req-1",
            "status": "IN_PROGRESS",
            "kraljic_quadrant": "LEVERAGE",
            "strategy": "COMPETITIVE_AUCTION",
        },
        bids=[{"bid_id": "b1", "supplier_id": "s1", "amount": 2400}],
        names={"s1": "Acme Corp"},
    )

    state = await op.poll_once(nid)

    assert state["status"] == "IN_PROGRESS"
    assert state["quadrant"] == "LEVERAGE"
    assert state["strategy"] == "COMPETITIVE_AUCTION"
    assert state["bids"][0]["supplier_name"] == "Acme Corp"


@pytest.mark.asyncio
async def test_total_cost_usd_passed_through_when_present(monkeypatch):
    nid = _fresh_negotiation_id()
    _stub_reads(
        monkeypatch,
        neg={"requisition_id": "req-1", "status": "IN_PROGRESS", "total_cost_usd": 2.14},
    )

    state = await op.poll_once(nid)

    assert state["total_cost_usd"] == 2.14


@pytest.mark.asyncio
async def test_total_cost_usd_is_none_before_any_agent_call(monkeypatch):
    nid = _fresh_negotiation_id()
    _stub_reads(monkeypatch, neg={"requisition_id": "req-1", "status": "PENDING"})

    state = await op.poll_once(nid)

    assert state["total_cost_usd"] is None


@pytest.mark.asyncio
async def test_existing_supplier_name_is_not_overwritten(monkeypatch):
    nid = _fresh_negotiation_id()
    _stub_reads(
        monkeypatch,
        neg={"requisition_id": "req-1", "status": "IN_PROGRESS"},
        bids=[{"bid_id": "b1", "supplier_id": "s1", "supplier_name": "Explicit Name", "amount": 5}],
        names={"s1": "Looked Up Name"},
    )

    state = await op.poll_once(nid)

    assert state["bids"][0]["supplier_name"] == "Explicit Name"


@pytest.mark.asyncio
async def test_status_change_fires_once_then_is_deduped(monkeypatch):
    nid = _fresh_negotiation_id()
    _stub_reads(monkeypatch, neg={"requisition_id": "req-1", "status": "PENDING"})
    q = op.subscribe(nid)

    await op.poll_once(nid)
    first_events = [e["event"] for e in await _drain(q)]
    assert "status_change" in first_events

    await op.poll_once(nid)  # status unchanged
    second_events = [e["event"] for e in await _drain(q)]
    assert "status_change" not in second_events


@pytest.mark.asyncio
async def test_status_change_fires_again_when_status_actually_changes(monkeypatch):
    nid = _fresh_negotiation_id()
    _stub_reads(monkeypatch, neg={"requisition_id": "req-1", "status": "PENDING"})
    q = op.subscribe(nid)
    await op.poll_once(nid)
    await _drain(q)

    _stub_reads(monkeypatch, neg={"requisition_id": "req-1", "status": "IN_PROGRESS"})
    await op.poll_once(nid)
    events = await _drain(q)

    change_events = [e for e in events if e["event"] == "status_change"]
    assert len(change_events) == 1
    assert change_events[0]["previous_status"] == "PENDING"
    assert change_events[0]["status"] == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_offer_received_fires_once_for_a_new_priced_bid(monkeypatch):
    nid = _fresh_negotiation_id()
    _stub_reads(
        monkeypatch,
        neg={"requisition_id": "req-1", "status": "EVALUATING"},
        bids=[{"bid_id": "b1", "supplier_id": "s1", "amount": 100}],
    )
    q = op.subscribe(nid)

    await op.poll_once(nid)
    events = [e["event"] for e in await _drain(q)]

    assert events.count("offer_received") == 1


@pytest.mark.asyncio
async def test_offer_received_not_refired_for_unchanged_bid(monkeypatch):
    nid = _fresh_negotiation_id()
    _stub_reads(
        monkeypatch,
        neg={"requisition_id": "req-1", "status": "EVALUATING"},
        bids=[{"bid_id": "b1", "supplier_id": "s1", "amount": 100}],
    )
    q = op.subscribe(nid)
    await op.poll_once(nid)
    await _drain(q)

    await op.poll_once(nid)  # identical bid on the next poll cycle
    events = [e["event"] for e in await _drain(q)]

    assert "offer_received" not in events


@pytest.mark.asyncio
async def test_award_issued_fires_once_per_new_award(monkeypatch):
    nid = _fresh_negotiation_id()
    _stub_reads(
        monkeypatch,
        neg={"requisition_id": "req-1", "status": "APPROVED"},
        awards=[{"award_id": "a1", "supplier_name": "Acme Corp", "total_amount": 2400}],
        orders=[
            {"order_id": "o1", "requisition_id": "req-1"}
        ],  # present so no synthetic order path
    )
    q = op.subscribe(nid)

    await op.poll_once(nid)
    events = [e["event"] for e in await _drain(q)]
    assert events.count("award_issued") == 1

    await op.poll_once(nid)  # same award on the next cycle
    events = [e["event"] for e in await _drain(q)]
    assert "award_issued" not in events


@pytest.mark.asyncio
async def test_po_issued_fires_once_per_new_order(monkeypatch):
    nid = _fresh_negotiation_id()
    _stub_reads(
        monkeypatch,
        neg={"requisition_id": "req-1", "status": "COMPLETED"},
        orders=[{"order_id": "o1", "requisition_id": "req-1", "total_value": 2400}],
    )
    q = op.subscribe(nid)

    await op.poll_once(nid)
    events = [e["event"] for e in await _drain(q)]
    assert events.count("po_issued") == 1

    await op.poll_once(nid)  # same order on the next cycle
    events = [e["event"] for e in await _drain(q)]
    assert "po_issued" not in events


@pytest.mark.asyncio
async def test_classification_defined_fires_once_when_quadrant_first_appears(monkeypatch):
    nid = _fresh_negotiation_id()
    _stub_reads(monkeypatch, neg={"requisition_id": "req-1", "status": "PENDING"})
    q = op.subscribe(nid)
    await op.poll_once(nid)  # no quadrant/strategy yet
    await _drain(q)

    _stub_reads(
        monkeypatch,
        neg={
            "requisition_id": "req-1",
            "status": "PENDING",
            "kraljic_quadrant": "STRATEGIC",
            "strategy": "PARTNERSHIP_VALUE",
        },
    )
    await op.poll_once(nid)
    events = [e for e in await _drain(q) if e["event"] == "classification_defined"]

    assert len(events) == 1
    assert events[0]["quadrant"] == "STRATEGIC"
    assert events[0]["strategy"] == "PARTNERSHIP_VALUE"

    await op.poll_once(nid)  # unchanged classification on the next cycle
    events = [e for e in await _drain(q) if e["event"] == "classification_defined"]
    assert events == []
