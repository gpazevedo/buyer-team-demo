import { useEffect, useRef, useState } from "react";
import OfferCard from "./OfferCard";
import ApprovalControls from "./ApprovalControls";

type NegotiationState = {
  negotiation_id: string;
  requisition_id: string | null;
  status: string | null;
  quadrant: string | null;
  strategy: string | null;
  approval_block_reason: string | null;
  bids: Bid[];
  awards: Award[];
  orders: Order[];
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

export default function Timeline({ negotiationId }: { negotiationId: string | null }) {
  const [state, setState] = useState<NegotiationState | null>(null);
  const [events, setEvents] = useState<string[]>([]);

  useEffect(() => {
    if (!negotiationId) return;

    console.log("[Timeline] watching negotiation", negotiationId);

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
        if (evt.event === "award_issued") {
          setState((prev) => prev ? {
            ...prev,
            awards: [...(prev.awards || []), {
              award_id: evt.award_id,
              supplier_name: evt.supplier_name,
              total_amount: evt.total_amount,
              savings_amount: evt.savings_amount,
            }],
          } : prev);
        }
        if (evt.event === "po_issued") {
          setState((prev) => prev ? {
            ...prev,
            orders: [...(prev.orders || []), {
              order_id: evt.order_id,
              supplier_name: evt.supplier_name,
              total_value: evt.total_value,
              status: evt.status || "ISSUED",
            }],
          } : prev);
        }
        // Refresh state on updates
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
    };
  }, [negotiationId]);

  // Polling fallback: refresh state every 3s so the UI stays in sync even
  // if SSE events are missed (the SSE stream has no guaranteed delivery).
  const pollRef = useRef<ReturnType<typeof setInterval>>(undefined);
  useEffect(() => {
    if (!negotiationId) return;
    pollRef.current = setInterval(() => {
      fetch(`/demo/negotiations/${negotiationId}`)
        .then((r) => r.json())
        .then(setState)
        .catch(() => {});
    }, 3000);
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
      <div className="mb-6">
        <h2 className="text-2xl font-bold mb-2">Negotiation Timeline</h2>
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
      </div>

      {/* Progress bar */}
      {state && (
        <div className="mb-8">
          <ProgressBar status={state.status} />
        </div>
      )}

      {/* Bids / Offers */}
      {state?.bids && state.bids.length > 0 && (
        <section className="mb-8">
          <h3 className="text-lg font-semibold mb-3">Supplier Offers</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {state.bids.map((bid) => (
              <OfferCard key={bid.bid_id} bid={bid} />
            ))}
          </div>
        </section>
      )}

      {/* Approval Controls */}
      {state?.status === "PENDING_APPROVAL" && state.requisition_id && (
        <section className="mb-8">
          <ApprovalControls
            requisitionId={state.requisition_id}
            blockReason={state.approval_block_reason}
          />
        </section>
      )}

      {/* Award */}
      {state?.awards && state.awards.length > 0 && (
        <section className="mb-8">
          <h3 className="text-lg font-semibold mb-3">Award</h3>
          {state.awards.map((a) => (
            <div key={a.award_id} className="p-4 rounded-lg bg-gray-800 border border-gray-700">
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
        <section className="mb-8">
          <h3 className="text-lg font-semibold mb-3">Purchase Order</h3>
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
  const statusOrder = ["PENDING", "IN_PROGRESS", "EVALUATING", "PENDING_APPROVAL", "APPROVED", "COMPLETED"];
  const currentIdx = status ? statusOrder.indexOf(status) : 0;

  return (
    <div className="flex items-center gap-1">
      {steps.map((step, i) => {
        const done = i <= currentIdx || (status === "COMPLETED" && i === steps.length - 1);
        const active = i === currentIdx && status !== "COMPLETED";
        return (
          <div key={step.key} className="flex items-center gap-1 flex-1">
            <div className={`flex-1 h-1.5 rounded-full ${done ? "bg-blue-500" : active ? "bg-blue-400 animate-pulse" : "bg-gray-700"}`} />
            <span className={`text-[10px] whitespace-nowrap ${done ? "text-blue-400" : active ? "text-blue-300" : "text-gray-600"}`}>
              {step.label}
            </span>
          </div>
        );
      })}
    </div>
  );
}
