import { useEffect, useState } from "react";

type AuditEvent = {
  ts: string;
  event_id: string;
  actor: string;
  event_type: string;
  source_layer: string;
  detail: Record<string, unknown>;
};

type AuditResponse = {
  negotiation_id: string;
  count: number;
  agent_tier_reached: boolean;
  events: AuditEvent[];
};

/** Which AWS runtime produced the row. This is the point of the panel: a
 *  decision came either from an orchestrator node Lambda or from a Bedrock
 *  AgentCore agent, and the trail records which. */
const LAYERS: Record<string, { label: string; style: string }> = {
  lambda_core: {
    label: "λ Lambda",
    style: "border-sky-700 text-sky-300 bg-sky-900/20",
  },
  buyer_agent_core: {
    label: "Bedrock AgentCore",
    style: "border-violet-700 text-violet-300 bg-violet-900/20",
  },
};

/** Actors read as `orchestrator.node.kraljic_classify` / `agent:kraljic-classifier`;
 *  the prefix is already conveyed by the layer badge. */
function shortActor(actor: string): string {
  return (actor ?? "").replace(/^orchestrator\.node\./, "").replace(/^agent:/, "");
}

function time(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toISOString().slice(11, 19);
}

export default function AuditTrail({ negotiationId }: { negotiationId: string }) {
  const [data, setData] = useState<AuditResponse | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const fetchTrail = () => {
      fetch(`/demo/negotiations/${negotiationId}/audit`)
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error("audit unavailable"))))
        .then((d) => {
          if (!cancelled) setData(d);
        })
        .catch(() => {
          if (!cancelled) setData(null);
        });
    };
    fetchTrail();
    const id = setInterval(fetchTrail, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [negotiationId]);

  const count = data?.count ?? 0;

  return (
    <section className="mb-4">
      <div className="flex items-center justify-between mb-1.5">
        <h3 className="text-base font-semibold">Decision Trail</h3>
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-600">DynamoDB · append-only</span>
          <span className="px-2 py-0.5 rounded border border-gray-700 bg-gray-800 text-xs font-mono text-gray-300">
            {count} events
          </span>
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            className="text-xs text-gray-500 hover:text-blue-400 transition-colors"
          >
            {open ? "Collapse" : "Expand"}
          </button>
        </div>
      </div>

      <div className="p-3 rounded-lg bg-gray-800/50 border border-gray-700">
        {count === 0 ? (
          <p className="text-sm text-gray-500">
            No decision events recorded yet for this negotiation.
          </p>
        ) : (
          <>
            {!data?.agent_tier_reached && (
              <p className="text-xs text-amber-400/80 mb-2">
                Orchestrator (Lambda) decisions only — the agent tier did not run for this
                negotiation, so there are no Bedrock AgentCore rows.
              </p>
            )}
            {open ? (
              <ul className="space-y-1.5">
                {data!.events.map((e) => {
                  const layer = LAYERS[e.source_layer];
                  return (
                    <li
                      key={e.event_id}
                      className="p-2 rounded border border-gray-700 bg-gray-900/40"
                    >
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-[11px] font-mono text-gray-500">{time(e.ts)}</span>
                        <span
                          className={`px-1.5 py-0.5 rounded border text-[10px] ${
                            layer?.style ?? "border-gray-700 text-gray-400"
                          }`}
                        >
                          {layer?.label ?? e.source_layer}
                        </span>
                        <span className="text-xs font-mono text-gray-300">
                          {shortActor(e.actor)}
                        </span>
                        <span className="text-[11px] text-gray-500">{e.event_type}</span>
                      </div>
                      <pre className="mt-1 text-[10px] text-gray-500 overflow-x-auto whitespace-pre-wrap break-all">
                        {JSON.stringify(e.detail, null, 2)}
                      </pre>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="text-sm text-gray-500">
                {count} immutable decision events recorded — every LLM decision, tool call and
                orchestrator-node decision behind this negotiation. Expand to read them.
              </p>
            )}
          </>
        )}
      </div>
    </section>
  );
}
