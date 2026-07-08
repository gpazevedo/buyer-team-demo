from demo_harness.health import _classify_pricing_mode


def test_fallback_when_newest_bid_is_fallback():
    bids = [
        {"source": "spot_fallback_stub", "created_at": "2026-07-08T10:00:00Z"},
        {"source": "spot_bidding_agent", "created_at": "2026-07-08T09:00:00Z"},
    ]
    result = _classify_pricing_mode(bids)
    assert result == {"pricing_mode": "fallback", "source": "spot_fallback_stub"}


def test_live_when_newest_bid_is_agent_priced():
    bids = [
        {"source": "spot_bidding_agent", "created_at": "2026-07-08T10:00:00Z"},
        {"source": "spot_fallback_stub", "created_at": "2026-07-08T09:00:00Z"},
    ]
    result = _classify_pricing_mode(bids)
    assert result == {"pricing_mode": "live", "source": "spot_bidding_agent"}


def test_live_when_newest_bid_is_ceiling_clamp():
    bids = [
        {"source": "leverage_auction_agent_ceiling_clamp", "created_at": "2026-07-08T10:00:00Z"},
    ]
    result = _classify_pricing_mode(bids)
    assert result == {"pricing_mode": "live", "source": "leverage_auction_agent_ceiling_clamp"}


def test_unknown_when_only_informative_sources():
    bids = [
        {"source": "auto_priced", "created_at": "2026-07-08T10:00:00Z"},
        {"source": "supplier_response_seed", "created_at": "2026-07-08T09:00:00Z"},
    ]
    result = _classify_pricing_mode(bids)
    assert result == {"pricing_mode": "unknown", "source": None}


def test_unknown_when_empty_list():
    result = _classify_pricing_mode([])
    assert result == {"pricing_mode": "unknown", "source": None}


def test_newest_decides_regardless_of_older():
    bids = [
        {"source": "bottleneck_negotiation_agent", "created_at": "2026-07-08T10:00:00Z"},
        {"source": "bottleneck_fallback_stub", "created_at": "2026-07-08T09:30:00Z"},
        {"source": "spot_fallback_stub", "created_at": "2026-07-08T09:00:00Z"},
    ]
    result = _classify_pricing_mode(bids)
    assert result == {"pricing_mode": "live", "source": "bottleneck_negotiation_agent"}


def test_skips_informative_and_finds_fallback():
    bids = [
        {"source": "auto_priced", "created_at": "2026-07-08T10:00:00Z"},
        {"source": "auto_priced", "created_at": "2026-07-08T09:45:00Z"},
        {"source": "strategic_fallback_stub", "created_at": "2026-07-08T09:30:00Z"},
    ]
    result = _classify_pricing_mode(bids)
    assert result == {"pricing_mode": "fallback", "source": "strategic_fallback_stub"}


def test_live_with_each_agent_source():
    agents = [
        "spot_bidding_agent",
        "leverage_auction_agent",
        "strategic_partnership_agent",
        "bottleneck_negotiation_agent",
    ]
    for source in agents:
        result = _classify_pricing_mode([{"source": source}])
        assert result["pricing_mode"] == "live", f"Expected {source} to be live"
