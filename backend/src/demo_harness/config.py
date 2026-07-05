"""Demo harness configuration — env-driven, matches PRD-020 §6."""

from __future__ import annotations

import os
from uuid import NAMESPACE_DNS, uuid5

ENV = os.getenv("ENV", "dev")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
SKILL_MODE = os.getenv("SKILL_MODE", "live")

TENANT_ID = str(uuid5(NAMESPACE_DNS, "root:tenant:blue-jets"))

# Seam S1 — master-store intake (reused from test_tenant_app)
MASTER_STORE_TABLE = os.getenv(
    "MASTER_STORE_TABLE", f"{ENV}-test-tenant-master-purchase-requisitions"
)

# DynamoDB domain tables (read-only observation, S2/S3/S5)
COMMUNICATIONS_TABLE = os.getenv("COMMUNICATIONS_TABLE", f"{ENV}-communications")
BIDS_TABLE = os.getenv("BIDS_TABLE", f"{ENV}-bids")
NEGOTIATIONS_TABLE = os.getenv("NEGOTIATIONS_TABLE", f"{ENV}-negotiations")
AWARDS_TABLE = os.getenv("AWARDS_TABLE", f"{ENV}-awards")
ORDERS_TABLE = os.getenv("ORDERS_TABLE", f"{ENV}-orders")
AGENT_SESSION_CACHE_TABLE = os.getenv("AGENT_SESSION_CACHE_TABLE", f"{ENV}-agent-session-cache")
REQUISITIONS_TABLE = os.getenv("REQUISITIONS_TABLE", f"{ENV}-requisitions")
ITEMS_TABLE = os.getenv("ITEMS_TABLE", f"{ENV}-items")
SUPPLIERS_TABLE = os.getenv("SUPPLIERS_TABLE", f"{ENV}-suppliers")
CATEGORIES_TABLE = os.getenv("CATEGORIES_TABLE", f"{ENV}-categories")
CATEGORY_SUPPLIERS_TABLE = os.getenv("CATEGORY_SUPPLIERS_TABLE", f"{ENV}-category-suppliers")
TENANTS_TABLE = os.getenv("TENANTS_TABLE", f"{ENV}-tenants")

# Seam S4 — HITL approval release (direct Lambda invoke)
APPROVAL_GATE_FUNCTION = os.getenv(
    "APPROVAL_GATE_FUNCTION", f"{ENV}-buyer-team-node6-approval-gate"
)

# Poll intervals
OBSERVER_POLL_SECONDS = int(os.getenv("OBSERVER_POLL_SECONDS", "2"))

# Display-only; no server-side enforcement (PRD-020 v1.2.0 §5.2.4)
DEFAULT_DEADLINE_MINUTES = int(os.getenv("DEFAULT_DEADLINE_MINUTES", "5"))
