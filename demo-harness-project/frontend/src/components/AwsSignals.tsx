import { useEffect, useState } from "react";

type Tile = {
  label: string;
  value: number | null;
  unit: string;
  namespace: string;
  dashboard_url: string;
};

type MetricsResponse = {
  window_minutes: number;
  metrics: Record<string, Tile>;
};

/** A metric that hasn't been published yet is blank, never 0 — EMF surfaces in
 *  ~30s, and a real zero would misread as "nothing happened". */
function format(value: number | null, unit: string): string {
  if (value === null || value === undefined) return "—";
  switch (unit) {
    case "usd":
      return `$${value.toFixed(2)}`;
    case "s":
      return value >= 60 ? `${(value / 60).toFixed(1)}m` : `${value.toFixed(1)}s`;
    case "ratio":
      return `${(value * 100).toFixed(0)}%`;
    default:
      return Number.isInteger(value) ? String(value) : value.toFixed(1);
  }
}

export default function AwsSignals({ negotiationId }: { negotiationId?: string }) {
  const [data, setData] = useState<MetricsResponse | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const fetchMetrics = () => {
      fetch("/demo/metrics")
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error("metrics unavailable"))))
        .then((d) => {
          if (!cancelled) {
            setData(d);
            setFailed(false);
          }
        })
        .catch(() => {
          if (!cancelled) setFailed(true);
        });
    };
    fetchMetrics();
    const id = setInterval(fetchMetrics, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
    // negotiationId only re-triggers the poll; the metrics are tenant-scoped.
  }, [negotiationId]);

  const tiles = data ? Object.entries(data.metrics) : [];

  return (
    <section className="mb-4">
      <div className="flex items-center justify-between mb-1.5">
        <h3 className="text-base font-semibold">Live AWS Signals</h3>
        <span className="text-xs text-gray-600">
          Amazon CloudWatch
          {data ? ` · last ${data.window_minutes}m` : ""}
        </span>
      </div>
      <div className="p-3 rounded-lg bg-gray-800/50 border border-gray-700">
        {failed ? (
          <p className="text-sm text-gray-500">CloudWatch unavailable.</p>
        ) : tiles.length === 0 ? (
          <p className="text-sm text-gray-500">Loading CloudWatch metrics...</p>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
            {tiles.map(([key, t]) => (
              <a
                key={key}
                href={t.dashboard_url}
                target="_blank"
                rel="noreferrer"
                title={`${t.namespace} — open the CloudWatch dashboard`}
                className="block p-2 rounded border border-gray-700 bg-gray-900/40 hover:border-blue-700 transition-colors"
              >
                <div
                  className={`text-lg font-mono ${
                    t.value === null ? "text-gray-600" : "text-gray-100"
                  }`}
                >
                  {format(t.value, t.unit)}
                </div>
                <div className="text-[11px] text-gray-400 leading-tight">{t.label}</div>
                <div className="text-[9px] text-gray-600 font-mono truncate">{t.namespace}</div>
              </a>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
