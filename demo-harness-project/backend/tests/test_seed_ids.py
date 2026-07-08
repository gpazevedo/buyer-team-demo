"""uuid5-based id generators — must be deterministic (same input -> same id,
byte-for-byte) so `seed()` stays idempotent across repeated runs."""

from demo_harness.seed import _category_id, _item_id, _supplier_id


def test_category_id_is_deterministic():
    assert _category_id("LEVERAGE") == _category_id("LEVERAGE")


def test_category_id_is_case_insensitive():
    assert _category_id("LEVERAGE") == _category_id("leverage")


def test_category_id_differs_per_quadrant():
    ids = {_category_id(q) for q in ("NON_CRITICAL", "LEVERAGE", "BOTTLENECK", "STRATEGIC")}
    assert len(ids) == 4


def test_item_id_is_deterministic():
    assert _item_id("BJ-32-MWTIRE") == _item_id("BJ-32-MWTIRE")


def test_item_id_differs_per_sku():
    assert _item_id("BJ-32-MWTIRE") != _item_id("BJ-23-VHFXCVR")


def test_supplier_id_is_deterministic():
    assert _supplier_id("AeroStock Intl") == _supplier_id("AeroStock Intl")


def test_supplier_id_differs_per_name():
    assert _supplier_id("AeroStock Intl") != _supplier_id("SkyParts Distribution")
