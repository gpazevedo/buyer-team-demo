import { useEffect, useState } from "react";

type HealthCheck = {
  healthy: boolean;
  checks: Record<string, string>;
  identity?: { account: string; region: string; env: string };
  pricing_mode?: string;
  pricing_mode_source?: string | null;
};

/** Each AWS service this harness actually talks to, and the health-check keys
 *  that stand for it. Named explicitly so the header says "Lambda / DynamoDB /
 *  Step Functions / Bedrock AgentCore" rather than one anonymous green dot. */
const SERVICES: { label: string; keys: string[] }[] = [
  { label: "AWS Lambda", keys: ["approval_gate_lambda"] },
  { label: "DynamoDB", keys: ["master_store_table", "requisitions_table"] },
  { label: "Step Functions", keys: ["step_functions"] },
  { label: "Bedrock AgentCore", keys: ["agentcore_runtimes"] },
];

function Dot({ ok }: { ok: boolean }) {
  return <span className={`w-2 h-2 rounded-full ${ok ? "bg-green-500" : "bg-red-500"}`} />;
}

export default function BuyerTeamStatus() {
  const [health, setHealth] = useState<HealthCheck | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const res = await fetch("/demo/health");
        const data: HealthCheck = await res.json();
        if (cancelled) return;
        console.log("[BuyerTeamStatus] health check:", data);
        setHealth(data);
      } catch (e) {
        console.error("[BuyerTeamStatus] health check failed:", e);
        if (!cancelled) setHealth({ healthy: false, checks: { request: `error: ${e}` } });
      }
    }

    poll();
    const id = setInterval(poll, 15_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (!health) {
    return (
      <span className="flex items-center gap-1.5 text-xs text-gray-500">
        <span className="w-2 h-2 rounded-full bg-gray-600 animate-pulse" />
        checking Buyer Team...
      </span>
    );
  }

  const id = health.identity;

  return (
    <span className="flex items-center gap-3 flex-wrap">
      {id && (
        <span
          className="px-2 py-0.5 rounded bg-gray-800 border border-gray-700 text-xs text-gray-300"
          title={`This demo is running against a real AWS account (${id.account}) in ${id.region}.`}
        >
          AWS {id.region} · {id.env} · acct {id.account}
        </span>
      )}

      <span
        className="flex items-center gap-1.5 text-xs text-gray-400 cursor-help"
        title={Object.entries(health.checks)
          .map(([k, v]) => `${k}: ${v}`)
          .join("\n")}
      >
        <Dot ok={health.healthy} />
        Buyer Team {health.healthy ? "reachable" : "unreachable"}
      </span>

      {SERVICES.map(({ label, keys }) => {
        const values = keys.map((k) => health.checks[k]).filter((v) => v !== undefined);
        // A service with no reported check yet is unknown, not broken.
        if (values.length === 0) return null;
        const ok = values.every((v) => v === "ok");
        return (
          <span
            key={label}
            className="flex items-center gap-1.5 text-xs text-gray-400 cursor-help"
            title={keys.map((k) => `${k}: ${health.checks[k] ?? "unknown"}`).join("\n")}
          >
            <Dot ok={ok} />
            {label}
          </span>
        );
      })}

      <span
        className="flex items-center gap-1.5 text-xs text-gray-400 cursor-help"
        title={
          health.pricing_mode_source
            ? `source: ${health.pricing_mode_source}`
            : "No recent bids to classify"
        }
      >
        <span
          className={`w-2 h-2 rounded-full ${
            health.pricing_mode === "live"
              ? "bg-green-500"
              : health.pricing_mode === "fallback"
                ? "bg-amber-500"
                : "bg-gray-600"
          }`}
        />
        {health.pricing_mode === "live"
          ? "LLM agents reachable"
          : health.pricing_mode === "fallback"
            ? "Fallback pricing (VPC/NAT down)"
            : "No recent bids"}
      </span>
    </span>
  );
}
