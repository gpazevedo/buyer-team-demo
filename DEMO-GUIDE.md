# Buyer Team Demo Guide

This guide walks through a live demonstration of the Buyer Team procurement lifecycle
from PR to PO, using the demo-harness React app alongside the AWS CloudWatch dashboard
and X-Ray tracing. It is designed for a hands-on walkthrough — someone clicks "Submit PR"
while you narrate what happens in the orchestrator and the observability layer.

---

## Prerequisites

```bash
# From impl/
uv sync

# Seed Blue Jets tenant (idempotent — safe to re-run if already seeded)
uv run --package demo-harness python -m demo_harness.seed
```

**Start the backend** (one shell):

```bash
cd demo-harness-project/backend
SKILL_MODE=live ENV=dev AWS_REGION=us-east-1 \
  uv run uvicorn demo_harness.main:app --reload --port 8000
```

**Start the frontend** (separate shell):

```bash
cd demo-harness-project/frontend
pnpm install
pnpm dev          # → http://localhost:5174
```

**Open the AWS dashboard** in a browser tab:

```
https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards:name=dev-buyer-team-business
```

---

## Quick-start demo (3 minutes)

1. Open `http://localhost:5174` — the header shows a green "Buyer Team reachable" badge.
2. Pick **Strategic** (HPT blade set, $96k/unit), quantity 1, click **Submit PR**.
3. You are auto-switched to the **Timeline** tab. The progress bar shows: Ingest → Strategy → Evaluate → Approval → Award → PO Issued.
4. Watch offers arrive from TurbineTech OEM (the only STRATEGIC supplier). Each new offer pops in via SSE.
5. The timeline pauses at **PENDING_APPROVAL** with a yellow "Human Approval Required" panel. Click **Approve**.
6. The negotiation transitions through APPROVED → AWARDED → COMPLETED. A green "Purchase Order" section appears.
7. Switch to the CloudWatch dashboard tab — widgets show data within 30 seconds (high-resolution 1-second metrics). The "Negotiations Started vs Completed" widget now shows 1 completed.
8. Open X-Ray with the procurement filter to see the connected trace across all 6 nodes: `https://us-east-1.console.aws.amazon.com/xray/home?region=us-east-1#/traces?filter=annotation.procurement.tenant_id%20IS%20NOT%20NULL`

---

## Demo Walkthrough

### 1. Create a PR from the UI

Open `http://localhost:5174`, stay on the **New PR** tab.

Four Kraljic quadrants are available, each driving a different orchestrator path:

| Quadrant | Part | Strategy | What to watch |
|----------|------|----------|---------------|
| Non-Critical | Lavatory consumable kit ($180) | SPOT_BID | Auto-approves. Completes in ~30s. No approval panel appears. |
| Leverage | Main wheel tire ($2,400) | COMPETITIVE_AUCTION | HITL only if awarded price >$5k (set qty ≥3). See competing bids from AeroStock, SkyParts, GlobalWheel. |
| Bottleneck | VHF COMM transceiver ($11,800) | PARTNERSHIP_RISK | Always pauses for HITL approval. Block reason reads "STRATEGIC_APPROVAL_REQUIRED". |
| Strategic | HPT stage-1 blade set ($96,000) | PARTNERSHIP_VALUE | Always pauses for HITL. Highest value — best savings story. |

**Verification via the API** (useful mid-demo):
```bash
# Check what's running
curl -s localhost:8000/demo/health | python3 -m json.tool

# List all Blue Jets PRs
curl -s localhost:8000/demo/requisitions | python3 -m json.tool

# Peek at a specific negotiation
curl -s localhost:8000/demo/negotiations/{negotiation_id} | python3 -m json.tool
```

### 2. Watch the negotiation evolve in real time

After submitting a PR, the **Timeline** tab opens an SSE stream to the backend's
`offer_projection` poll loop, which reads from the real DynamoDB tables every 2
seconds. No mock data — every update is the real orchestrator writing to `{env}-bids`,
`{env}-communications`, and the `{env}-negotiations` domain table.

**What you see, in order:**

1. **Progress bar** — light-blue filled segments advance: Ingest → Strategy → Evaluate → Approval → Award → PO Issued. The active segment pulses.
2. **Quadrant + strategy badges** — e.g. `STRATEGIC` (red) + `PARTNERSHIP_VALUE`.
3. **Supplier Offers** — cards appear as bids land, showing supplier name, amount, delivery days, evaluation rank. Resilience-fallback bids are tagged `source: <strategy>_fallback_stub`.
4. **Human Approval Required** (for BOTTLENECK/STRATEGIC / high-value LEVERAGE) — yellow panel with Approve / Cycle Back / Reject buttons. Block reason shown above.
5. **Award** — green card with awarded supplier, total amount, and savings.
6. **Purchase Order** — dark-green section confirming PO issued with PO ID and total value.
7. **Event log** — expandable detail section at the bottom showing every SSE event as JSON.

**The status flow:**
```
ACTIVE → NEGOTIATING → EVALUATING → PENDING_APPROVAL → APPROVED → AWARDED → COMPLETED
```

Approximate duration per quadrant (with VPC/NAT up for LLM agents):

| Quadrant | Time to PENDING_APPROVAL | Time to COMPLETED (if approved fast) |
|----------|-------------------------|--------------------------------------|
| NON_CRITICAL | ~15s | ~30s (auto, no approval pause) |
| LEVERAGE | ~30s | ~45s (+decision time if over $5k) |
| BOTTLENECK | ~60s | ~75s (+decision time) |
| STRATEGIC | ~90s | ~105s (+decision time) |

Without VPC (NAT down — resilience fallback pricing), all quadrants complete within
these bounds; bids show `source: <strategy>_fallback_stub` instead of `agent_priced`.

**Performance note:** Node 5 (bid evaluation) and Node 7 (award communications) use
inline deterministic logic — no A2A agent overhead. Each completes in ~1-2s instead
of the 25-60s the LLM agents previously added. The per-quadrant times above reflect
this improvement.

### 3. Approve (or reject) a negotiation

When the timeline reaches `PENDING_APPROVAL`, **three decisions** are available:

- **Approve** — releases the Step Functions Approval Gate. State machine continues to
  AWARDED → COMPLETED. A PO is issued.
- **Cycle Back** — sends the negotiation back to the strategy agent for re-evaluation
  with revised terms.
- **Reject** — terminates the negotiation. The status changes to REJECTED and no PO
  is created.

The decision calls `test_tenant_app`'s `GraphClient.approve_award()` / `.reject_award()`
/ `.cycle_back_award()` — the same code the `test_tenant_app` UI uses, which invokes
the Node 6 approval-gate Lambda directly via `boto3 lambda.invoke`.

### 4. Check the Suppliers tab

The **Suppliers** tab shows the Blue Jets supplier roster and, for each supplier,
their RFQ history: which negotiations they were invited to, the bid they submitted,
and the award/rejection feedback they received.

The five suppliers:

| Supplier | Quadrants | Specialization |
|----------|-----------|----------------|
| AeroStock Intl | NON_CRITICAL, LEVERAGE | Cabin consumables, tires |
| SkyParts Distribution | NON_CRITICAL, LEVERAGE | General MRO parts |
| TurbineTech OEM | STRATEGIC, BOTTLENECK | Engine LLPs, avionics (highest quality scores) |
| Avionics Prime | BOTTLENECK | Avionics LRUs |
| GlobalWheel Co | LEVERAGE | Wheels and tires |

---

## AWS Observability Walkthrough

### 1. CloudWatch Business Dashboard

URL: `https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards:name=dev-buyer-team-business`

Nine widgets with **30-second refresh** (high-resolution metrics at 1-second
storage resolution) reading the `procurement/business` namespace (emitted by
`orchestrator/resilience/metrics.py` across all node Lambdas):

| Widget | What it shows during a demo |
|--------|----------------------------|
| **Negotiations Started vs Completed (by strategy)** | After each PR: a new "started" point appears. After approval+completion: a "completed" point follows. Breakout by strategy (SPOT_BID, COMPETITIVE_AUCTION, PARTNERSHIP_RISK, PARTNERSHIP_VALUE). |
| **Negotiation Cycle Time (p50 / p99)** | How long negotiations take from start to completion. |
| **Bids per Negotiation (avg)** | SPOT_BID → 1 bid (single invite). Leverage → 2-3 bids (multi-supplier auction). |
| **Governance Compliance Rate (avg)** | Running at 1.0 (100%) — no governance violations in the demo path. |
| **Governance Violations (by type, stacked)** | Stays flat at 0 in a clean demo. Spikes if you trigger a guardrail breach. |
| **Approvals (by status, stacked)** | Every HITL decision registers here — APPROVED, REJECTED, CYCLE_BACK. |
| **Approval Wait Time (avg / p90)** | How long the negotiation sat in PENDING_APPROVAL before someone clicked Approve. |
| **Negotiation Savings — Amount / Pct** | Dollar savings and percentage calculated against estimated unit price vs awarded amount. Best demo story: STRATEGIC HPT blade — $96k est. vs ~$88k awarded = ~8% savings. |
| **Token Usage — Input / Output (by agent + model tier)** | LLM token consumption across all 6 agents during this negotiation. Spikes visibly when the strategy/negotiation agents run. |
| **Kraljic Classification Source (by source, stacked)** | Shows how each PR was classified — `agent` (LLM-driven), `semantic_cache` (cache hit), `rule_based_fallback` (resilience path). |

**Live demo script for the dashboard:**

```text
"Every widget on this dashboard is driven by the same procurement business metrics —
  not infrastructure metrics like CPU or memory. This is a business dashboard showing
  negotiations, bids, approvals, savings, and compliance in real time.

  [Submit a STRATEGIC PR] — watch 'Negotiations Started' tick up.
  [Wait for PENDING_APPROVAL] — 'Approval Wait Time' starts climbing.
  [Click Approve] — 'Negotiations Completed' ticks up, 'Approvals' shows APPROVED,
  'Savings Amount' registers the dollar savings from the LLM negotiation."
```

### 2. X-Ray Connected Trace (PR→PO)

URL: `https://us-east-1.console.aws.amazon.com/xray/home?region=us-east-1#/traces?filter=annotation.procurement.tenant_id%20IS%20NOT%20NULL`

Each PR triggers a Step Functions execution. All 6 node Lambdas now share a single
X-Ray trace — W3C trace context propagates from the PR Event Router into the SFN
input, and each node forwards it to the next via the `_otel_ctx` field.

Custom spans with domain attributes (`procurement.tenant_id`, `procurement.negotiation_id`)
connect the full flow:

```
node.ingest_validate → node.kraljic_classify → node.strategy_execute
  → node.bid_evaluation → node.approval_gate → node.award_comms
```

Nodes 1-4 that invoke A2A agents (ingest, classify, strategy-execute, approval-gate)
show an `agentcore.invoke` sub-span for the LLM call. Nodes 5 and 7 (bid-evaluation,
award-comms) use inline deterministic logic — they complete in ~1-2s with only the
node span and ADOT-instrumented DynamoDB reads/writes. The ADOT Lambda
auto-instrumentation captures SDK calls across all 6 nodes.

**To find a trace:**

1. Open X-Ray → **Traces** or use the link above.
2. Filter by time range matching the demo.
3. Use the **X-Ray group** `dev-procurement` (created in the observability module)
   or filter by `annotation.procurement.tenant_id IS NOT NULL`.
4. Open a trace — the waterfall shows all 6 node spans in sequence.

**Pro tip:** Sort by duration (longest first) — the STRATEGIC quadrant negotiation
has the richest span detail (multi-round LLM negotiation).

### 3. CloudWatch Logs

Key log groups:

| Log group | What's in it |
|-----------|-------------|
| `/aws/vendedlogs/states/dev-buyer-team-procurement` | Step Functions execution events — each state transition, input/output |
| `/aws/vendedlogs/bedrock-agentcore/dev-receiving-gateway-*` | Receiving Gateway AgentCore runtime logs |
| `aws/spans` | X-Ray Transaction Search spans (indexed for full-text search across traces) |

**CloudWatch Logs Insights query** for demo verification:

```sql
# Show all procurement workflow events for the last 15 minutes
fields @timestamp, @message
| filter logGroup = "/aws/vendedlogs/states/dev-buyer-team-procurement"
| sort @timestamp desc
| limit 20
```

### 4. Agent Runtime Alarms

12 CloudWatch alarms cover the 6 AgentCore agent runtimes + 6 orchestrator node
Lambdas + the Step Functions state machine:

- `dev-buyer-team-{agent-name}-agent-errors` — fires on any AgentCore runtime error
- `dev-buyer-team-{agent-name}-agent-latency` — fires on invocation >60s
- `dev-buyer-team-{node-name}-errors` — fires on any node Lambda error
- `dev-buyer-team-procurement-executions-failed` — fires on any failed SFN execution
- `dev-buyer-team-procurement-executions-timed-out` — fires on any timed-out execution

---

## Running through all four quadrants

To demonstrate the full range of Buyer Team behaviour, run one PR per quadrant
consecutively. The 2-second poll loop handles all of them in parallel — each
negotiation's timeline updates independently.

**Suggested demo order (best narrative arc):**

1. **NON_CRITICAL** — "The simplest path: a low-value, low-risk item. Auto-classified,
   auto-approved, PO issued in ~30 seconds. No human needed."
2. **LEVERAGE (qty=1)** — "Multiple suppliers compete. No HITL needed because the
   awarded price stays under the $5k gate."
3. **LEVERAGE (qty=3, ~$7.2k)** — "Same item, higher quantity — now over the $5k
   threshold. The governance gate pauses for approval."
4. **BOTTLENECK** — "A sole-source avionics part. Partnership strategy, always
   requires human approval regardless of price."
5. **STRATEGIC** — "The crown jewel: engine LLP, $96k. Two agents negotiate with
   TurbineTech OEM, governance audits the result, human approves. This is the full
   power of Buyer Team — LLM-negotiated savings on a critical, high-value part."

After all five, the dashboard shows 5 started, 5 completed (or 4 if you rejected one),
with savings accumulated across multiple strategies.

---

## What to do if something doesn't work

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| "Buyer Team unreachable" in header | Seed missing or tables not deployed | Run `uv run --package demo-harness python -m demo_harness.seed`; check `terraform apply` |
| 404 on negotiation after PR submit | PR created but orchestrator hasn't started processing yet | Wait 5-10s and refresh the Timeline tab |
| No invites/bids after 60s | VPC/NAT down (LLM agents can't reach Bedrock) | Resilience fallback kicks in automatically — bids arrive tagged `fallback_stub`; or restore VPC |
| Timeline SSE disconnects | Backend restarted (--reload) | Refresh the page |
| Dashboard widgets show "No data" | No negotiations completed today, or the IAM role for the emitting component doesn't include the `procurement/business` namespace in its `cloudwatch:PutMetricData` condition | Submit a PR and let it finish — data appears within 30 seconds. If it doesn't, the IAM policy for that Lambda's role needs the namespace added (see `infra/modules/step-functions/main.tf` step-invoker policy or `infra/agent_runtimes.tf` agent-runtime policy) |



## Appendix: Console URLs (dev, us-east-1)

```
CloudWatch Business Dashboard (30s refresh):
  https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards:name=dev-buyer-team-business

X-Ray Traces — Connected PR→PO trace (filter by tenant_id annotation):
  https://us-east-1.console.aws.amazon.com/xray/home?region=us-east-1#/traces?filter=annotation.procurement.tenant_id%20IS%20NOT%20NULL

X-Ray Traces — Procurement group:
  https://us-east-1.console.aws.amazon.com/xray/home?region=us-east-1#/traces?groupName=dev-procurement

Step Functions (procurement workflow):
  https://us-east-1.console.aws.amazon.com/states/home?region=us-east-1#/statemachines/view/arn:aws:states:us-east-1:234876310489:stateMachine:dev-buyer-team-procurement

DynamoDB Tables (dev-*):
  https://us-east-1.console.aws.amazon.com/dynamodb/home?region=us-east-1#tables:
```
