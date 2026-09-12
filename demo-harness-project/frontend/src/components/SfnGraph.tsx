import { useEffect, useState } from "react";

type SfnStateStatus = "pending" | "running" | "succeeded" | "failed";

type SfnGraphState = {
  execution_status: string;
  states: { name: string; status: SfnStateStatus }[];
  url: string | null;
};

const STATE_STYLES: Record<SfnStateStatus, string> = {
  pending: "border-gray-700 text-gray-500 bg-gray-800/60",
  running: "border-amber-500 text-amber-300 bg-amber-900/20 animate-pulse",
  succeeded: "border-green-500/70 text-green-300 bg-green-900/20",
  failed: "border-red-500 text-red-300 bg-red-900/20",
};

/** Which AWS primitive actually executes each state. Note `RouteStrategy` and
 *  `Done` are Step Functions states in their own right — there is no Lambda
 *  behind them — and there is no node4. Names verified against the deployed
 *  `dev-buyer-team-node*` functions. */
const STATE_RUNTIME: Record<string, { short: string; full: string }> = {
  IngestValidate: {
    short: "λ node1-ingest-validate",
    full: "AWS Lambda — dev-buyer-team-node1-ingest-validate",
  },
  KraljicClassify: {
    short: "λ node2 → AgentCore",
    full: "AWS Lambda — dev-buyer-team-node2-kraljic-classify\ninvokes Bedrock AgentCore (Kraljic Classifier agent)",
  },
  RouteStrategy: {
    short: "SFN Choice",
    full: "Step Functions Choice state — no Lambda",
  },
  StrategyExecute: {
    short: "λ node3 → AgentCore",
    full: "AWS Lambda — dev-buyer-team-node3-strategy-execute\ninvokes Bedrock AgentCore (strategy agent)",
  },
  BidEvaluation: {
    short: "λ node5-bid-evaluation",
    full: "AWS Lambda — dev-buyer-team-node5-bid-evaluation (inline deterministic)",
  },
  ApprovalGate: {
    short: "λ node6 · waitForTaskToken",
    full: "AWS Lambda — dev-buyer-team-node6-approval-gate\nStep Functions waitForTaskToken (can wait up to 96h)",
  },
  AwardComms: {
    short: "λ node7-award-comms",
    full: "AWS Lambda — dev-buyer-team-node7-award-comms (inline deterministic)",
  },
  Done: {
    short: "SFN Succeed",
    full: "Step Functions Succeed state — no Lambda",
  },
};

const STATUS_BADGES: Record<string, string> = {
  RUNNING: "bg-amber-900/50 text-amber-300 border-amber-700",
  SUCCEEDED: "bg-green-900/50 text-green-300 border-green-700",
  FAILED: "bg-red-900/50 text-red-300 border-red-700",
  TIMED_OUT: "bg-red-900/50 text-red-300 border-red-700",
  ABORTED: "bg-gray-800 text-gray-400 border-gray-700",
};

export default function SfnGraph({ negotiationId }: { negotiationId: string }) {
  const [graph, setGraph] = useState<SfnGraphState | null>(null);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const fetchGraph = () => {
      fetch(`/demo/negotiations/${negotiationId}/sfn`)
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error("execution not found"))))
        .then((g) => {
          if (!cancelled) {
            setGraph(g);
            setNotFound(false);
          }
        })
        .catch(() => {
          if (!cancelled) setNotFound(true);
        });
    };
    fetchGraph();
    const id = setInterval(fetchGraph, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [negotiationId]);

  const statusBadge =
    (graph && STATUS_BADGES[graph.execution_status]) || "bg-gray-800 text-gray-400 border-gray-700";

  return (
    <section className="mb-4">
      <div className="flex items-center justify-between mb-1.5">
        <h3 className="text-base font-semibold">Step Functions Execution</h3>
        <div className="flex items-center gap-2">
          {graph && (
            <span className={`px-2 py-0.5 rounded border text-xs font-mono ${statusBadge}`}>
              {graph.execution_status}
            </span>
          )}
          {graph?.url && (
            <a
              href={graph.url}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-gray-500 hover:text-blue-400 transition-colors"
            >
              SFN Console ↗
            </a>
          )}
        </div>
      </div>
      <div className="p-3 rounded-lg bg-gray-800/50 border border-gray-700">
        {graph && Array.isArray(graph.states) && graph.states.length > 0 ? (
          <div className="flex flex-wrap items-center gap-1.5">
            {graph.states.map((s, i) => (
              <div key={s.name} className="flex items-center gap-1.5">
                {i > 0 && <span className="text-gray-600 text-xs">→</span>}
                <span
                  className={`px-2 py-1 rounded border text-[11px] font-mono whitespace-nowrap ${
                    STATE_STYLES[s.status] || STATE_STYLES.pending
                  }`}
                  title={STATE_RUNTIME[s.name]?.full}
                >
                  {s.name}
                  {STATE_RUNTIME[s.name] && (
                    <span className="block text-[9px] text-gray-500 font-normal">
                      {STATE_RUNTIME[s.name].short}
                    </span>
                  )}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-gray-500">
            {notFound
              ? "Waiting for the Step Functions execution to start..."
              : "Loading Step Functions execution..."}
          </p>
        )}
      </div>
    </section>
  );
}
