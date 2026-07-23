# Buyer Team — Lifecycle Demo Harness

Standalone FastAPI + React 19 app that demonstrates the full Buyer Team procurement
lifecycle (PR → Kraljic classification → RFQ/negotiation → bid evaluation → HITL
approval → PO) end-to-end for a dedicated "Blue Jets" (aviation MRO) tenant — without
modifying the orchestrator. See `PRDs/PRD-020-lifecycle-demo-harness-impl.md` for the
full seam-by-seam design.

It observes and drives the real orchestrator through its existing seams only:

- **Intake** — reuses `test_tenant_app`'s `MasterDataClient.create_pr` (same client the
  live `test_tenant_app` `/api/requisitions` route calls). Writing a PR to the master
  store is what starts the real DynamoDB-Stream → `pr_event_router` → Step Functions
  chain — this harness never calls `StartExecution` directly.
- **Observation** — polls `{env}-negotiations` / `{env}-bids` / `{env}-communications`
  every 1 second and pushes changes over SSE. All DynamoDB I/O runs in `asyncio.to_thread()`
  with 5 s timeouts to avoid blocking the uvicorn event loop. Read-only; never writes
  to `{env}-bids`.
- **HITL approval** — reuses `test_tenant_app`'s `GraphClient`, which releases a paused
  Approval Gate via a direct `boto3 lambda.invoke` of the Node 6 Lambda. There is no
  HTTP approval API in the orchestrator.
  After approval, the tab and negotiation ID are preserved across the page reload via
  `sessionStorage` so the user lands back on the Timeline.

## Layout

```text
backend/src/demo_harness/
  config.py           env-driven settings (table names, tenant id, poll interval)
  health.py           /demo/health checks (approval-gate Lambda, master-store tables, SFN) + pricing-mode detection
  seed.py             idempotent Blue Jets tenant/category/item/supplier seed
  reset_demo.py       idempotent Blue Jets runtime data cleanup (keeps seed data)
  pr_generator.py     builds + submits a PR for a given Kraljic quadrant
  offer_projection.py background poll loop + in-memory per-negotiation projection
  observer.py         FastAPI routes: health, negotiation snapshot/stream, approve, requisitions, suppliers, traces
  main.py             app wiring, lifespan (starts the poll loop), /demo/seed admin routes

frontend/src/
  App.tsx                        tab shell (New PR / Requisitions / Timeline / Suppliers)
  components/BuyerTeamStatus.tsx header badges: Buyer Team reachability + pricing-mode (live/fallback), polls /demo/health
  components/UtcClock.tsx        header clock showing local time labeled with its UTC offset
  components/PRForm.tsx          quadrant + quantity form -> POST /demo/requisitions
  components/PRList.tsx          Requisitions tab: expandable PR list with per-PR negotiation state, links into Timeline
  components/Timeline.tsx        SSE-driven negotiation view (bids, approval, award, PO)
  components/ApprovalControls.tsx Approve / Reject / Cycle Back buttons
  components/OfferCard.tsx       one supplier's bid
  components/SupplierInbox.tsx   Blue Jets supplier roster
```

## Prerequisites

- Python 3.14, [uv](https://docs.astral.sh/uv/)
- Node + [pnpm](https://pnpm.io/)
- AWS credentials for the target `ENV` (default `dev`) — DynamoDB read/write and
  `lambda:InvokeFunction` on `{env}-buyer-team-node6-approval-gate`

This backend is a member of the `impl` uv workspace (`test-tenant-app` is a workspace
dependency, not vendored) — run `uv sync` from `impl/`, not from this directory alone.

## Run

```bash
# from impl/
uv sync

# 1. Seed the Blue Jets tenant (idempotent — safe to re-run)
uv run --package demo-harness python -m demo_harness.seed
# or once the backend is up: curl -X POST localhost:8000/demo/seed

# 1b. (Optional) Reset runtime data from prior demo cycles
uv run --package demo-harness python -m demo_harness.reset_demo

# 2. Backend
cd demo-harness-project/backend
SKILL_MODE=live ENV=dev AWS_REGION=us-east-1 uv run uvicorn demo_harness.main:app --reload --port 8000

# 3. Frontend (separate shell)
cd demo-harness-project/frontend
pnpm install
pnpm dev   # http://localhost:5174, proxies /demo/* to :8000
```

## Testing

```bash
# Backend (pytest — excludes tests/integration, which needs real AWS creds)
cd demo-harness-project/backend
uv run --extra dev pytest

# Frontend (Vitest + React Testing Library)
cd demo-harness-project/frontend
pnpm install
pnpm test
```

## Creating a Purchase Requisition

**Via the UI:** open `http://localhost:5174`, stay on the **New PR** tab, pick a Kraljic
quadrant, set a quantity, and click **Submit PR**. Each quadrant maps to one fixed Blue
Jets aviation part and drives a different orchestrator strategy:

| Quadrant | Part | Strategy | Approval |
| --- | --- | --- | --- |
| `NON_CRITICAL` | Lavatory service consumable kit | `SPOT_BID` | auto-approved (low value) |
| `LEVERAGE` | Main wheel tire, radial | `COMPETITIVE_AUCTION` | HITL if awarded price > $5k |
| `BOTTLENECK` | VHF COMM transceiver | `PARTNERSHIP_RISK` | always HITL |
| `STRATEGIC` | HPT stage-1 blade set (LLP) | `PARTNERSHIP_VALUE` | always HITL |

On submit you're switched to the **Timeline** tab, which opens an SSE stream for the
returned `negotiation_id` and updates live as the orchestrator processes it — offers
arriving, status changes, and (for `BOTTLENECK`/`STRATEGIC`, or `LEVERAGE` over $5k) an
**Approve / Reject / Cycle Back** panel once the negotiation reaches
`PENDING_APPROVAL`.

The **Requisitions** tab lists every Blue Jets PR (`GET /demo/requisitions`); click one to
expand its line items and current negotiation state (bids, award, PO) inline, or jump
straight into its live Timeline.

**Via the API directly:**

```bash
curl -X POST localhost:8000/demo/requisitions \
  -H 'Content-Type: application/json' \
  -d '{"quadrant": "NON_CRITICAL", "quantity": 1}'
# -> {"requisition_id": "...", "negotiation_id": "...", "tenant_id": "...", "quadrant": "NON_CRITICAL", "item": {...}, "created_at": "..."}
```

This calls `test_tenant_app`'s `MasterDataClient.create_pr` under the hood — the same
write path the live `test_tenant_app` UI uses — so it's a real PR, not a simulated one.
The Blue Jets tenant must be seeded first (see **Run**, step 1) or the quadrant's item
won't resolve.

**Watching it happen:** tail the backend's stdout — every step logs
(`creating PR quadrant=... quantity=...` → `PR created requisition_id=... negotiation_id=...`
→ `offer received negotiation=... supplier=... amount=...` → `status change ... -> ...`),
or poll the snapshot directly: `curl localhost:8000/demo/negotiations/{negotiation_id}`.
The browser console mirrors every frontend action the same way (`[PRForm]`, `[Timeline]`,
`[ApprovalControls]`, prefixed by component).

## Configuration

All env vars have working defaults for `dev`; override only what you need to point
elsewhere (see `backend/src/demo_harness/config.py`):

| Var | Default | Purpose |
| --- | --- | --- |
| `ENV` | `dev` | DynamoDB table prefix |
| `AWS_REGION` | `us-east-1` | |
| `SKILL_MODE` | `live` | `test_tenant_app` clients read this at import time — must be `live` for real AWS calls |
| `APPROVAL_GATE_FUNCTION` | `{ENV}-buyer-team-node6-approval-gate` | HITL release target |
| `OBSERVER_POLL_SECONDS` | `1` | background projection poll interval |

## Notes / known limits

- **No VPC required for the demo to complete.** Only the 6 LLM AgentCore agent
  runtimes are VPC-bound; if VPC/NAT is down, Node 3/4x's own resilience layer falls
  back to deterministic pricing (bids tagged `source: <strategy>_fallback_stub`) and
  the rest of the lifecycle (ingest → classify → evaluate → approve → PO) still runs
  to completion — just without real LLM-negotiated offers.
- **Synthetic POs for auto-priced flows.** When the orchestrator skips the
  `award_comms` node (auto-approved strategies), the demo harness creates a synthetic
  Purchase Order so the UI shows a PO step. If a real orchestrator order arrives later,
  the synthetic is automatically deleted. The PO section always shows the canonical
  order.
- **Status normalization.** The orchestrator writes raw statuses (`ACTIVE`, `AWARDED`,
  `AUTO_APPROVED`); the `dynamo_client` normalizes them to the app contract
  (`ACTIVE`→`IN_PROGRESS`, `AWARDED`→`COMPLETED`, `AUTO_APPROVED`→`APPROVED`).
- **Idempotent SSE events.** All 7 event types (`classification_defined`, `rfq_sent`,
  `auction_round_feedback`, `offer_received`, `award_issued`, `po_issued`,
  `status_change`) have backend idempotency guards — concurrent poll cycles cannot
  produce duplicate events.
- **Strategy Classification** appears immediately on PR submission (before the backend
  confirms), showing the selected quadrant with a pulsing "classifying..." label until
  the strategy name arrives.
- Single PO per negotiation today — per-supplier PO grouping isn't implemented in the
  orchestrator yet (see PRD-020 §3.6).
- No persisted `deadline` field; `DEFAULT_DEADLINE_MINUTES` is display-only.
