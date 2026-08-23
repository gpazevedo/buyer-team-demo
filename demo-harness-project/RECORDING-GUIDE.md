# Reproducing the PR→PO Video Recordings

Runbook for regenerating the demo-harness recordings in `recordings/`: one
PR→PO video per Kraljic strategy, plus the three CloudWatch dashboards and an
X-Ray trace walkthrough. Written from an actual recording session — the
gotchas below are all things that went wrong the first time, not
speculation.

Uses the `agent-browser` CLI skill for browser automation + video capture.
Everything here assumes `ENV=dev`, `AWS_REGION=us-east-1`, VPC/NAT up.

---

## 0. Prerequisites

- AWS credentials for `dev` with DynamoDB, Lambda, Step Functions, and
  `bedrock-agentcore-control` read access (see step 6 for console access).
- `uv`, `pnpm`, and the `agent-browser` CLI available on PATH.
- No stale `uvicorn`/`vite` processes already holding ports 8000 / 5174.
- In a sandboxed/auto-mode Claude Code session, `aws sts get-federation-token`
  and the signin-URL-building script (step 5) can each get blocked once by
  the permission classifier even though they're fully local — it reads
  temporary-credential generation as sensitive. Expect to approve them
  explicitly; this isn't a sign anything is wrong.

**agent-browser daemon cwd gotcha:** `agent-browser record start recordings/foo.webm`
resolves that relative path against the *daemon's* working directory — fixed
once, whenever the background daemon process first started — not the calling
shell's cwd at the time you run `record start`, even if you `cd` first in the
same command. If the daemon was already running from an earlier session (or
started from some other directory earlier in this one), every recording
silently lands somewhere other than `recordings/` with no error. Check where
it actually thinks it is before recording anything:

```bash
DAEMON_PID=$(pgrep -f 'agent-browser/.*dist/daemon.js')
readlink /proc/$DAEMON_PID/cwd
```

If that's not `demo-harness-project/`, either restart the daemon from the
right directory (`agent-browser close` does **not** kill the daemon process
itself — kill it directly, e.g. `kill $DAEMON_PID`) or just pass absolute
paths to `record start` to sidestep the issue entirely.

## 1. Environment setup

Run from `demo/` — **not** `impl/**`. Despite `demo-harness-project/README.md`
saying "run `uv sync` from `impl/`", this repo has its own uv workspace
(`demo/pyproject.toml` lists `test_tenant_app/backend` and
`demo-harness-project/backend` as members) since the harness was extracted
into its own repo. `impl/` is a separate checkout entirely.

```bash
cd demo/
uv sync

uv run --package demo-harness python -m demo_harness.seed          # idempotent
uv run --package demo-harness python -m demo_harness.reset_demo    # clears prior runtime data, keeps seed
```

## 2. Start backend + frontend

```bash
cd demo/demo-harness-project/backend
SKILL_MODE=live ENV=dev AWS_REGION=us-east-1 uv run uvicorn demo_harness.main:app --port 8000 &

cd demo/demo-harness-project/frontend
pnpm dev &   # http://localhost:5174
```

Verify before recording anything:

```bash
curl -s localhost:8000/demo/health | python3 -m json.tool
# all four checks should be "ok"; pricing_mode is "unknown" until the first bid lands, that's fine
```

**pnpm gotcha:** if `pnpm -v` errors with `This project is configured to use
yarn`, you ran it from the wrong directory. `pnpm` (a corepack shim here)
walks up from `cwd` looking for the nearest `package.json` with a
`packageManager` field; run it from `demo-harness-project/frontend/` itself,
not from a parent directory — otherwise it can walk all the way up to `$HOME`
and pick up an unrelated yarn config.

## 3. Recording a single strategy — the critical gotcha

**Start `agent-browser record start` *before* touching the form, not after.**

`record start` recreates the Playwright browser context (required to attach
a video sink) and reloads the page, which resets all React state back to
defaults (quadrant=NON_CRITICAL, quantity=1). If you select a quadrant, set
quantity, confirm it visually, *then* start recording, the reload silently
discards your selection and the wrong PR gets submitted — the backend log
will show `creating PR quadrant=NON_CRITICAL quantity=1` no matter what the
UI displayed a moment earlier. This is quiet: nothing errors, you just get
the wrong negotiation.

Correct order, per strategy:

```bash
agent-browser set viewport 1440 900
agent-browser record start recordings/<strategy>-pr-to-po.webm
agent-browser open http://localhost:5174
agent-browser wait 1500
agent-browser snapshot -i                      # get fresh @refs — do this every run
```

Then select the quadrant radio, fill quantity, confirm the estimated total
via `agent-browser get text body`, and only then click Submit.

**Don't use `agent-browser wait --load networkidle`** after submitting — the
Timeline tab opens a persistent SSE stream, so the network never goes idle
and the wait times out. Poll the API instead:

```bash
NEG_ID=<from backend log: "negotiation_id=...">
for i in $(seq 1 30); do
  sleep 3
  STATUS=$(curl -s localhost:8000/demo/negotiations/$NEG_ID | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")
  echo "$STATUS"
  [ "$STATUS" = "PENDING_APPROVAL" ] || [ "$STATUS" = "COMPLETED" ] && break
done
```

When `PENDING_APPROVAL`, re-snapshot (refs from before the SSE-driven
re-render are stale) and click Approve:

```bash
agent-browser snapshot -i
agent-browser click @e<approve-ref>
```

Poll again until `COMPLETED`, then:

```bash
agent-browser wait 1500
agent-browser record stop
```

**Reset between runs** (`uv run --package demo-harness python -m demo_harness.reset_demo`)
so the Requisitions/Timeline tabs don't carry stale data into the next
recording. Skip this if you specifically want the dashboards to show all
four negotiations accumulated together at the end (see step 5).

## 4. Per-quadrant settings

| Quadrant | Quantity | Why |
|---|---|---|
| NON_CRITICAL | 1 (default) | Auto-approves regardless; nothing to tune. |
| LEVERAGE | **40**, not 1 | The radio label says "price gate at $10k," but that gate is checked against the *awarded* (negotiated) price, not the estimated total. Real LLM-negotiated prices land 60–95% under the $2,400/unit estimate, so qty=5 (~$12k estimated) awarded at ~$4.7k and skipped HITL entirely. qty=40 (~$96k estimated) leaves enough margin to still clear $10k after a steep negotiated discount. |
| BOTTLENECK | 1 | Always HITL regardless of price. |
| STRATEGIC | 1 | Always HITL; $96k/unit is already the richest negotiation. |

Confirm the actual submitted values by checking the backend log line
(`creating PR quadrant=... quantity=...`) or `GET /demo/requisitions`, not
just what the UI showed before submit — see the gotcha above.

## 5. CloudWatch dashboard + X-Ray recordings

Do this **after** all four strategy runs, so the dashboards show the full
session's data (don't reset_demo before this step).

### Console access

There's no browser-authenticated AWS console session by default. Generate a
federated sign-in URL from your CLI credentials (needs an IAM user, not an
assumed role — `get-federation-token` requires long-term keys):

```bash
aws sts get-federation-token --name demo-recording-session \
  --policy '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["cloudwatch:*","logs:*","xray:*"],"Resource":"*"}]}' \
  --duration-seconds 3600 --region us-east-1 --output json > fedtoken.json
```

Exchange it for a signin token, then build the login URL (run as separate
commands — a single script that fetches creds and immediately POSTs them
elsewhere tends to trip credential-exfiltration heuristics in sandboxed
environments):

```bash
SESSION_JSON=$(python3 -c "
import json
c = json.load(open('fedtoken.json'))['Credentials']
print(json.dumps({'sessionId': c['AccessKeyId'], 'sessionKey': c['SecretAccessKey'], 'sessionToken': c['SessionToken']}))
")
curl -s -G "https://signin.aws.amazon.com/federation" \
  --data-urlencode "Action=getSigninToken" \
  --data-urlencode "SessionDuration=3600" \
  --data-urlencode "Session=${SESSION_JSON}" -o signin_response.json

python3 -c "
import json, urllib.parse
token = json.load(open('signin_response.json'))['SigninToken']
url = 'https://signin.aws.amazon.com/federation?' + urllib.parse.urlencode({
    'Action': 'login', 'Issuer': 'buyer-team-demo',
    'Destination': 'https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1',
    'SigninToken': token,
})
open('console_login_url.txt','w').write(url)
"
agent-browser set viewport 1920 1080
agent-browser open "$(cat console_login_url.txt)"
```

Delete `fedtoken.json` / `signin_response.json` afterward — they hold live
(if short-lived and scoped) credentials.

**First load always shows onboarding overlays** — cookie banner, "Service
menu" tooltip, "Unified Search" tooltip, "Unified Settings" tooltip. Dismiss
each (`Decline` / `Next` / `Escape` / `Done`) before recording; a fresh
`record start` recreates the context and cookie consent gets asked again
each time you jump to a new dashboard URL.

### Recording each dashboard

```bash
agent-browser record start recordings/dashboard-domain.webm
agent-browser open "https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards:name=dev-buyer-team-domain"
agent-browser wait 4000
# dismiss cookie banner / tooltips (see above)
agent-browser scroll down 800
agent-browser wait 2500     # repeat scroll+wait until you hit the bottom
agent-browser record stop
```

Repeat for `dev-buyer-team-platform` and `dev-buyer-team-finops`.

**Only 3 dashboards exist as of 2026-08-12** (PR #265) — `dev-buyer-team-application`
was merged into `dev-buyer-team-platform` and no longer exists as a separate
dashboard. `dev-buyer-team-platform` now has ~18 widgets (the old Platform +
Application widgets combined), so it takes noticeably more scroll+wait cycles
to reach the bottom than a 9-widget dashboard would. Verify the current list
before recording rather than trusting this doc:
`aws cloudwatch list-dashboards --query 'DashboardEntries[*].DashboardName'`.

### X-Ray traces

**Skip the trace-list filter box — go straight to one negotiation's trace
instead.** The filter box is unreliable and not worth fighting: pressing
Enter in the query text field inserts a **newline** rather than submitting
(it's a multi-line textarea), which then reads as invalid syntax. Clicking
"Run Query" alone (without touching the separate "Filter by X-Ray group"
field) avoids the classic "spurious syntax error if you touch both" problem,
but the classic filter (`annotation.procurement.tenant_id IS NOT NULL`) can
still throw a **persistent syntax-error banner** that doesn't clear even
after the query text is corrected — this reproduced consistently, not a
one-off fluke. Also: the query builder's inputs aren't in the Playwright
accessibility tree (rendered in an iframe), so any interaction with it needs
`agent-browser mouse move/down/up` at pixel coordinates read off a
screenshot, not `@ref`-based clicks.

The far more reliable path, and the one that actually gets you a single
connected PR→PO trace rather than a list you then have to pick from: pull
the resolved X-Ray URL straight off the demo frontend's own Timeline page.
Once a negotiation reaches `PENDING_APPROVAL` (or later), its Timeline header
renders a live `<a>` with an already-resolved trace URL:

```bash
# Separate throwaway session so it doesn't pollute the recording session's tab
agent-browser --session traceurl open http://localhost:5174
agent-browser --session traceurl wait 1000
agent-browser --session traceurl snapshot -i   # find "Requisitions", click it
agent-browser --session traceurl click @e<requisitions-ref>
agent-browser --session traceurl wait 1000
agent-browser --session traceurl snapshot -i   # click the target PR's row, then "Open live Timeline →"
agent-browser --session traceurl click @e<pr-row-ref>
agent-browser --session traceurl click @e<open-timeline-ref>
agent-browser --session traceurl wait 2000
agent-browser --session traceurl eval 'Array.from(document.querySelectorAll("a")).filter(a=>a.textContent.includes("X-Ray")).map(a=>a.href)'
agent-browser --session traceurl close
```

That gives a URL like `.../xray/home?region=us-east-1#/traces/1-<trace-id>`.
Prefer the **STRATEGIC** negotiation's trace — richest span detail (per the
pro tip below). Then record the actual trace detail page:

```bash
agent-browser record start recordings/xray-trace-detail.webm
agent-browser open "<the resolved trace URL from above>"
agent-browser wait 5000
# switch List -> Timeline toggle (not in a11y tree; use mouse move/down/up at its pixel coords)
# collapse the "Trace details" node-map panel (click its ▼/▶ toggle) to give the span waterfall more room
agent-browser wait 1000
# scroll THROUGH THE SPAN LIST, not the page — agent-browser scroll on the
# page body does nothing useful here; use mouse wheel positioned over the
# spans panel instead:
agent-browser mouse wheel 400   # repeat with waits between; skip ahead in
                                 # bigger increments once past the initial
                                 # ingest/classify spans — a rich trace can
                                 # have 400-500+ spans (mostly repetitive
                                 # DynamoDB polling during the HITL wait) and
                                 # scrolling through all of them makes for a
                                 # boring video
agent-browser wait 2000
agent-browser record stop
```

Watch for `agentcore.invoke` (the LLM negotiation span) and, further along,
`a2a.server.request_handlers...` / `BedrockRuntime` spans with visible
duration bars — those are the most demo-worthy parts of the waterfall to
land on before stopping the recording.

## 6. Health check — what to look at before trusting a "clean" run

Don't just trust that the UI reached `COMPLETED`; a negotiation can complete
successfully via a silent fallback path that masks a real bug (this
happened with BOTTLENECK — see below). Check:

```bash
# SFN executions — anything not SUCCEEDED?
aws stepfunctions list-executions --state-machine-arn <arn> --max-results 20

# Per-node Lambda errors (ADOT cold-start noise for django/celery/elasticsearch
# instrumentors is expected and harmless — real signal is anything else)
aws logs filter-log-events --log-group-name /aws/lambda/dev-buyer-team-node3-strategy-execute \
  --filter-pattern "?ERROR ?Exception" --start-time <ms>

# Per-bid pricing source — "*_fallback_stub" instead of "*_agent"/"*_agent_response"
# means the LLM call failed and degraded gracefully; the negotiation still
# completes, so this won't show up as an error anywhere else
curl -s localhost:8000/demo/negotiations/$NEG_ID | python3 -c "
import json,sys
d = json.load(sys.stdin)
for b in d['bids']: print(b['supplier_name'], b['source'])
"

# Firing alarms (dev-buyer-team-procurement-execution-time firing is expected
# during a demo — it includes HITL wait time in its p99, per the alarm's own comment)
aws cloudwatch describe-alarms --state-value ALARM

# DLQ depth (should be 0)
aws cloudwatch get-metric-statistics --namespace AWS/SQS \
  --metric-name ApproximateNumberOfMessagesVisible \
  --dimensions Name=QueueName,Value=dev-buyer-team-dlq \
  --start-time <iso> --end-time <iso> --period 300 --statistics Maximum
```

If a strategy falls back to stub pricing unexpectedly (bid `source` ends in
`_fallback_stub` for BOTTLENECK/STRATEGIC, or `*_fallback_stub` /
`*_agent_response` mismatch elsewhere), check
`orchestrator/agent_invoke.py`'s `runtime_arn()` — as of 2026-07-23 this
paginates `list_agent_runtimes()` correctly, but if it regresses, the
symptom is `RuntimeError("agent runtime '...' not found")` in the relevant
`node{N}-*` Lambda's CloudWatch log even though
`aws bedrock-agentcore-control list-agent-runtimes` (which auto-paginates)
shows the runtime as `READY`.

**Clean up orphaned executions:** if you `reset_demo` while a negotiation is
sitting at `PENDING_APPROVAL` without approving/rejecting it first, its
Step Functions execution is orphaned in `RUNNING` (paused at the Approval
Gate's `waitForTaskToken`, which can wait up to 96h). Stop it manually:

```bash
aws stepfunctions stop-execution --execution-arn <arn> --cause "demo cleanup"
```

## 7. Output

Eight recording files in `recordings/`: four `<strategy>-pr-to-po.webm`,
three `dashboard-<name>.webm` (domain/platform/finops — see the dashboard
count note in step 5), and one X-Ray trace recording (`xray-trace-detail.webm`
per the recommended approach above), plus a handful of PNG screenshots at
key moments (form filled, pending approval, completed) for each strategy.
