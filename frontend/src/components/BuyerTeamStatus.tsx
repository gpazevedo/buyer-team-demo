import { useEffect, useState } from "react";

type HealthCheck = {
  healthy: boolean;
  checks: Record<string, string>;
};

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

  return (
    <span
      className="flex items-center gap-1.5 text-xs text-gray-400 cursor-help"
      title={Object.entries(health.checks)
        .map(([k, v]) => `${k}: ${v}`)
        .join("\n")}
    >
      <span className={`w-2 h-2 rounded-full ${health.healthy ? "bg-green-500" : "bg-red-500"}`} />
      Buyer Team {health.healthy ? "reachable" : "unreachable"}
    </span>
  );
}
