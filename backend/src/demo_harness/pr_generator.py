"""PR Generator — builds aviation MRO purchase requisitions and submits them
via the canonical master-store intake (Seam S1, PRD-020 §5.1).

Reuses test_tenant_app's MasterDataClient.create_pr — the same client the
live test_tenant_app /api/requisitions route calls. No reimplementation.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from test_tenant_app.clients.master_data_client import master_data_client

from demo_harness.config import TENANT_ID
from demo_harness.seed import ITEMS, _item_id

logger = logging.getLogger("demo_harness.pr_generator")

BLUE_JETS_ADDRESS = "Blue Jets MRO Hub, Hangar 4, JFK International, New York, NY 11430"


def build_pr(quadrant: str, quantity: int = 1) -> dict:
    """Build and submit a PR for the given Kraljic quadrant.

    Returns the PR dict with requisition_id, negotiation_id, etc.
    """
    quadrant = quadrant.upper()
    item_def = ITEMS.get(quadrant)
    if not item_def:
        raise ValueError(f"Unknown quadrant: {quadrant}. Must be one of {list(ITEMS)}")

    item_id = _item_id(item_def["sku"])
    items = [
        {
            "item_id": item_id,
            "sku": item_def["sku"],
            "name": item_def["name"],
            "quantity": quantity,
            "estimated_price": item_def["estimated_unit_price"],
        }
    ]

    logger.info("submitting PR sku=%s quadrant=%s quantity=%d", item_def["sku"], quadrant, quantity)
    pr = master_data_client.create_pr(
        tenant_id=TENANT_ID,
        items=items,
        delivery_address=BLUE_JETS_ADDRESS,
        delivery_threshold_days=item_def["lead_time_days"],
        delivery_ideal_days=max(1, item_def["lead_time_days"] // 2),
        budget_limit=item_def["estimated_unit_price"] * quantity,
    )

    # Compute the deterministic negotiation_id for the response
    from uuid import NAMESPACE_DNS, uuid5

    negotiation_id = str(uuid5(NAMESPACE_DNS, f"{TENANT_ID}:negotiation:{pr['requisition_id']}"))
    logger.info(
        "PR %s submitted to master store, expecting negotiation %s to start via pr_event_router",
        pr["requisition_id"],
        negotiation_id,
    )

    return {
        "requisition_id": pr["requisition_id"],
        "negotiation_id": negotiation_id,
        "tenant_id": TENANT_ID,
        "quadrant": quadrant,
        "item": {
            "sku": item_def["sku"],
            "name": item_def["name"],
            "ata": item_def["ata"],
            "quantity": quantity,
            "estimated_unit_price": item_def["estimated_unit_price"],
        },
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
    }
