import { useEffect, useRef, useState } from "react";
import OfferCard from "./OfferCard";
import ApprovalControls from "./ApprovalControls";
import SfnGraph from "./SfnGraph";

type NegotiationState = {
  negotiation_id: string;
  requisition_id: string | null;
  status: string | null;
  quadrant: string | null;
  strategy: string | null;
  approval_block_reason: string | null;
  total_cost_usd: number | null;
  invitations: Invitation[];
  bids: Bid[];
  awards: Award[];
  orders: Order[];
};

type Invitation = {
  communication_id: string;
  type: string;
  supplier_id: string;
  supplier_name: string;
  created_at?: string | number;
};

type Bid = {
  bid_id: string;
  supplier_id: string;
  supplier_name: string;
  amount: number;
  unit_price?: number;
  delivery_days?: number;
  currency: string;
  source?: string;
  status?: string;
  evaluation_rank?: number;
};

type Award = {
  award_id: string;
  supplier_name: string;
  total_amount: number;
  savings_amount: number;
};

type Order = {
  order_id: string;
  status: string;
  total_value: number;
  supplier_name: string;
};

const QUADRANT_COLORS: Record<string, string> = {
  NON_CRITICAL: "bg-green-900/50 text-green-300 border-green-700",
  LEVERAGE: "bg-blue-900/50 text-blue-300 border-blue-700",
  BOTTLENECK: "bg-amber-900/50 text-amber-300 border-amber-700",
  STRATEGIC: "bg-red-900/50 text-red-300 border-red-700",
};

export default function Timeline({ negotiationId, initialQuadrant }: { negotiationId: string | null; initialQuadrant: string | null }) {
  const [state, setState] = useState<NegotiationState | null>(null);
  const [events, setEvents] = useState<string[]>([]);
  const [traceUrls, setTraceUrls] = useState<{
    sfn: string | null;
    xray: string | null;
    dashboards: { platform: string | null; finops: string | null; business: string | null } | null;
  }>({
    sfn: null,
    xray: null,
    dashboards: null,
  });

  useEffect(() => {
    if (!negotiationId) return;

    sessionStorage.setItem("demo:negotiationId", negotiationId);
    console.log("[Timeline] watching negotiation", negotiationId);

    // Fetch trace URLs — X-Ray takes ~30-60s to index, poll until both are found
    let traceInterval: ReturnType<typeof setInterval> | undefined;
    const fetchTraces = () => {
      fetch(`/demo/negotiations/${negotiationId}/traces`)
        .then((r) => r.json())
        .then((urls) => {
          // Each field keeps its last known value if this poll didn't
          // resolve it (e.g. a transient describe_execution failure) —
          // dashboards is static so it always sticks from the first poll.
          setTraceUrls((prev) => ({
            sfn: urls.sfn || prev.sfn,
            xray: urls.xray || prev.xray,
            dashboards: urls.dashboards || prev.dashboards,
          }));
          if (urls.sfn && urls.xray) clearInterval(traceInterval);
        })
        .catch(() => {});
    };
    fetchTraces();
    traceInterval = setInterval(fetchTraces, 10000);

    // Initial snapshot
    fetch(`/demo/negotiations/${negotiationId}`)
      .then((r) => r.json())
      .then((s) => {
        console.log("[Timeline] initial snapshot", s);
        setState(s);
      })
      .catch((e) => console.error("[Timeline] initial snapshot failed", e));

    // SSE stream
    const es = new EventSource(`/demo/negotiations/${negotiationId}/stream`);
    es.onopen = () => console.log("[Timeline] SSE connected", negotiationId);
    es.onerror = (e) => console.error("[Timeline] SSE error", e);
    es.addEventListener("snapshot", (e) => {
      try {
        const s = JSON.parse(e.data);
        console.log("[Timeline] SSE snapshot", s);
        setState(s);
      } catch (err) {
        console.error("[Timeline] failed to parse snapshot event", err);
      }
    });
    es.addEventListener("update", (e) => {
      try {
        const evt = JSON.parse(e.data);
        console.log("[Timeline] SSE update", evt);
        setEvents((prev) => [...prev.slice(-50), JSON.stringify(evt)]);
        // Refetch the full snapshot rather than patching state field-by-field —
        // keeps a single source of truth and avoids racing the poll fallback below.
        fetch(`/demo/negotiations/${negotiationId}`)
          .then((r) => r.json())
          .then(setState)
          .catch((err) => console.error("[Timeline] refresh after update failed", err));
      } catch (err) {
        console.error("[Timeline] failed to parse update event", err);
      }
    });
    return () => {
      console.log("[Timeline] closing SSE", negotiationId);
      es.close();
      clearInterval(pollRef.current);
      clearInterval(traceInterval);
    };
  }, [negotiationId]);

  // Polling fallback: refresh state every 5s so the UI stays in sync even
  // if SSE events are missed (the SSE stream has no guaranteed delivery).
  const pollRef = useRef<ReturnType<typeof setInterval>>(undefined);
  useEffect(() => {
    if (!negotiationId) return;
    pollRef.current = setInterval(() => {
      fetch(`/demo/negotiations/${negotiationId}`)
        .then((r) => r.json())
        .then(setState)
        .catch(() => {});
    }, 5000);
    return () => clearInterval(pollRef.current);
  }, [negotiationId]);

  if (!negotiationId) {
    return (
      <div className="text-center py-12 text-gray-400">
        <p className="text-lg">No negotiation selected</p>
        <p className="text-sm mt-2">Create a PR to begin watching the lifecycle</p>
      </div>
    );
  }

  return (
    <div className="max-w-4xl">
      {/* Header */}
      <div className="mb-3">
        <h2 className="text-xl font-bold mb-1">Negotiation Timeline</h2>
        <div className="flex items-center gap-3 text-sm">
          <code className="text-gray-400 font-mono text-xs">{negotiationId}</code>
          {state?.quadrant && (
            <span className={`px-2 py-0.5 rounded border text-xs font-medium ${QUADRANT_COLORS[state.quadrant] || "bg-gray-800 text-gray-300"}`}>
              {state.quadrant}
            </span>
          )}
          {state?.strategy && (
            <span className="text-gray-400">{state.strategy}</span>
          )}
          {state?.status && (
            <span className={`px-2 py-0.5 rounded text-xs font-medium ${
              state.status === "PENDING_APPROVAL" ? "bg-yellow-900/50 text-yellow-300" :
              state.status === "COMPLETED" || state.status === "APPROVED" ? "bg-green-900/50 text-green-300" :
              "bg-gray-800 text-gray-300"
            }`}>
              {state.status}
            </span>
          )}
        </div>
        {state?.requisition_id && (
          <code className="text-xs text-gray-500 mt-1 block">PR: {state.requisition_id}</code>
        )}
        <div className="flex gap-3 mt-1">
          <span className="flex items-center gap-1">
            <span className={`w-1.5 h-1.5 rounded-full ${traceUrls.sfn ? "bg-green-500" : "bg-gray-600"}`} />
            {traceUrls.sfn ? (
              <a href={traceUrls.sfn} target="_blank" rel="noreferrer"
                 className="text-xs text-gray-500 hover:text-blue-400 transition-colors">
                SFN Trace ↗
              </a>
            ) : (
              <span className="text-xs text-gray-600">SFN</span>
            )}
          </span>
          <span className="flex items-center gap-1">
            <span className={`w-1.5 h-1.5 rounded-full ${traceUrls.xray ? "bg-green-500" : traceUrls.sfn ? "bg-red-500 animate-pulse" : "bg-gray-600"}`} />
            {traceUrls.xray ? (
              <a href={traceUrls.xray} target="_blank" rel="noreferrer"
                 className="text-xs text-gray-500 hover:text-blue-400 transition-colors">
                X-Ray Trace ↗
              </a>
            ) : (
              <span className="text-xs text-gray-600">
                {traceUrls.sfn ? "X-Ray pending..." : "X-Ray"}
              </span>
            )}
          </span>
        </div>
        <div className="flex items-center gap-2 mt-1">
          {state?.total_cost_usd != null && (
            <span className="text-xs text-gray-500">
              Est. Cost: ${state.total_cost_usd.toFixed(2)}
            </span>
          )}
          <span className="text-xs text-gray-600">Dashboards:</span>
          {[
            { label: "Platform", url: traceUrls.dashboards?.platform },
            { label: "FinOps", url: traceUrls.dashboards?.finops },
            { label: "Business", url: traceUrls.dashboards?.business },
          ].map((d) =>
            d.url ? (
              <a
                key={d.label}
                href={d.url}
                target="_blank"
                rel="noreferrer"
                className="px-2 py-0.5 rounded bg-gray-800 border border-gray-700 text-xs text-gray-300 hover:text-blue-400 hover:border-blue-700 transition-colors"
              >
                {d.label}
              </a>
            ) : (
              <span
                key={d.label}
                className="px-2 py-0.5 rounded bg-gray-800 border border-gray-700 text-xs text-gray-600"
              >
                {d.label}
              </span>
            )
          )}
        </div>
      </div>

      {/* Step Functions execution graph */}
      <SfnGraph negotiationId={negotiationId} />

      {/* Progress bar */}
      {state && (
        <div className="mb-4">
          <ProgressBar status={state.status} />
        </div>
      )}

      {/* Strategy Classification */}
      {(state?.quadrant || initialQuadrant) && (
        <section className="mb-4">
          <h3 className="text-base font-semibold mb-1.5">Strategy Classification</h3>
          <div className="p-3 rounded-lg bg-gray-800 border border-cyan-800">
            <div className="flex items-center gap-3">
              <span className={`px-2 py-1 rounded border text-sm font-medium ${
                QUADRANT_COLORS[state?.quadrant || initialQuadrant || ""] || "bg-gray-700 text-gray-300 border-gray-600"
              }`}>
                {state?.quadrant || initialQuadrant}
              </span>
              {state?.strategy ? (
                <>
                  <span className="text-gray-400 text-sm">→</span>
                  <span className="text-cyan-300 font-mono text-sm font-medium">{state.strategy}</span>
                </>
              ) : (
                <>
                  <span className="text-gray-400 text-sm">→</span>
                  <span className="text-gray-500 font-mono text-sm animate-pulse">classifying...</span>
                </>
              )}
            </div>
          </div>
        </section>
      )}

      {/* RFQ Sent */}
      {state?.invitations && state.invitations.length > 0 && (
        <section className="mb-4">
          <h3 className="text-base font-semibold mb-1.5">Suppliers Invited</h3>
          <div className="flex flex-wrap gap-2">
            {state.invitations.map((inv) => (
              <span
                key={inv.supplier_id}
                className="px-3 py-1.5 rounded-lg bg-blue-900/30 border border-blue-800 text-sm text-blue-300"
              >
                {inv.supplier_name}
              </span>
            ))}
          </div>
        </section>
      )}

      {/* Approval Controls — shown above the offer cards so it's never
          pushed below the fold by a long supplier list */}
      {state?.status === "PENDING_APPROVAL" && state.requisition_id && (
        <section className="mb-4">
          <ApprovalControls
            requisitionId={state.requisition_id}
            blockReason={state.approval_block_reason}
          />
        </section>
      )}

      {/* Award — shown above the supplier quotations so the outcome is the
          first thing visible, not buried below a long offer list */}
      {state?.awards && state.awards.length > 0 && (
        <section className="mb-4">
          <h3 className="text-base font-semibold mb-1.5">Award</h3>
          {state.awards.map((a) => (
            <div key={a.award_id} className="p-3 rounded-lg bg-gray-800 border border-gray-700">
              <div className="flex justify-between items-center">
                <div>
                  <div className="font-medium">{a.supplier_name}</div>
                  <div className="text-sm text-gray-400">
                    ${a.total_amount?.toLocaleString()} — Savings: ${a.savings_amount?.toLocaleString()}
                  </div>
                </div>
                <span className="text-green-400 text-sm font-medium">AWARDED</span>
              </div>
            </div>
          ))}
        </section>
      )}

      {/* PO Issued */}
      {state?.orders && state.orders.length > 0 && (
        <section className="mb-4">
          <h3 className="text-base font-semibold mb-1.5">Purchase Order</h3>
          {state.orders.map((o) => (
            <div key={o.order_id} className="p-4 rounded-lg bg-green-950/30 border border-green-800">
              <div className="flex justify-between items-center">
                <div>
                  <div className="font-medium">{o.supplier_name}</div>
                  <div className="text-sm text-gray-400">
                    PO: {o.order_id} — ${o.total_value?.toLocaleString()}
                  </div>
                </div>
                <span className="text-green-400 text-sm font-medium">PO ISSUED</span>
              </div>
            </div>
          ))}
        </section>
      )}

      {/* Bids / Offers */}
      {state?.bids && state.bids.length > 0 && (
        <section className="mb-4">
          <h3 className="text-base font-semibold mb-1.5">Supplier Offers</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {state.bids.map((bid) => (
              <OfferCard key={bid.bid_id} bid={bid} />
            ))}
          </div>
        </section>
      )}

      {/* Event log */}
      {events.length > 0 && (
        <details className="mt-8">
          <summary className="text-sm text-gray-500 cursor-pointer hover:text-gray-400">
            Event log ({events.length})
          </summary>
          <pre className="mt-2 text-xs text-gray-500 max-h-48 overflow-auto bg-gray-900 p-3 rounded">
            {events.join("\n")}
          </pre>
        </details>
      )}
    </div>
  );
}

function ProgressBar({ status }: { status: string | null }) {
  const steps = [
    { key: "PENDING", label: "Ingest" },
    { key: "IN_PROGRESS", label: "Strategy" },
    { key: "EVALUATING", label: "Evaluate" },
    { key: "PENDING_APPROVAL", label: "Approval" },
    { key: "APPROVED", label: "Award" },
    { key: "COMPLETED", label: "PO Issued" },
  ];

  // Backend normalizes orchestrator statuses (dynamo_client.py _NEG_STATUS):
  //   ACTIVE/NEGOTIATING → IN_PROGRESS, AWARDED → COMPLETED
  // Other statuses (EVALUATING, PENDING_APPROVAL, APPROVED) pass through.
  const statusOrder = ["PENDING", "IN_PROGRESS", "EVALUATING", "PENDING_APPROVAL", "APPROVED", "AUTO_APPROVED", "COMPLETED"];
  const currentIdx = status ? statusOrder.indexOf(status) : 0;

  return (
    <div className="flex items-center gap-1">
      {steps.map((step, i) => {
        const done = i <= currentIdx || (status === "COMPLETED" && i === steps.length - 1);
        const active = i === currentIdx && status !== "COMPLETED";
        return (
          <div key={step.key} className="flex items-center gap-1 flex-1">
            <div className={`flex-1 h-1.5 rounded-full ${done ? "bg-blue-500" : active ? "bg-blue-400 animate-pulse" : "bg-gray-700"}`} />
            <span className={`text-xs whitespace-nowrap ${done ? "text-blue-400" : active ? "text-blue-300" : "text-gray-600"}`}>
              {step.label}
            </span>
          </div>
        );
      })}
    </div>
  );
}
