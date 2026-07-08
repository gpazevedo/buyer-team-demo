import { useEffect, useState } from "react";

type Supplier = {
  tenant_id: string;
  supplier_id: string;
  name: string;
  status: string;
  cage_code?: string;
  quality_score?: number;
  risk_rating?: string;
};

type Communication = {
  communication_id: string;
  type: string;
  created_at: string | number;
  subject?: string;
  current_rank?: number;
  price_improvement_needed?: boolean;
};

type Bid = {
  bid_id: string;
  amount: number;
  unit_price?: number;
  delivery_days?: number;
  currency: string;
  source?: string;
  status?: string;
};

type RfqEntry = {
  negotiation_id: string;
  status: string | null;
  quadrant: string | null;
  invitations: Communication[];
  feedback: Communication[];
  response: Bid | null;
};

function ts(value: string | number | undefined): number {
  if (!value) return 0;
  return typeof value === "number" ? value : new Date(value).getTime() / 1000;
}

function formatTimestamp(value: string | number | undefined): string {
  if (!value) return "unknown time";
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  return date.toLocaleString();
}

function feedbackColor(type: string): string {
  if (type === "AWARD_NOTIFICATION") return "bg-green-900/50 text-green-300";
  if (type === "REJECTION_NOTIFICATION") return "bg-red-900/50 text-red-300";
  return "bg-amber-900/50 text-amber-300";
}

async function fetchSuppliers(): Promise<Supplier[]> {
  const res = await fetch("/demo/suppliers");
  return res.json();
}

async function fetchRfqs(supplierId: string): Promise<RfqEntry[]> {
  const res = await fetch(`/demo/suppliers/${supplierId}/rfqs`);
  return res.json();
}

export default function SupplierInbox() {
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [rfqs, setRfqs] = useState<RfqEntry[] | null>(null);
  const [rfqsLoading, setRfqsLoading] = useState(false);

  useEffect(() => {
    fetchSuppliers()
      .then((data) => {
        console.log("[SupplierInbox] loaded suppliers", data.length);
        setSuppliers(data);
      })
      .catch((e) => console.error("[SupplierInbox] failed to load suppliers", e));
  }, []);

  function toggle(supplier: Supplier) {
    console.log("[SupplierInbox] clicked supplier", supplier.name);
    if (expanded === supplier.supplier_id) {
      setExpanded(null);
      setRfqs(null);
      return;
    }
    setExpanded(supplier.supplier_id);
    setRfqs(null);
    setRfqsLoading(true);
    fetchRfqs(supplier.supplier_id)
      .then((data) => {
        console.log("[SupplierInbox] RFQs for", supplier.name, data);
        setRfqs(data);
      })
      .catch((e) => console.error("[SupplierInbox] failed to load RFQs", e))
      .finally(() => setRfqsLoading(false));
  }

  if (suppliers.length === 0) {
    return (
      <div className="text-center py-12 text-gray-400">
        <p>No suppliers loaded</p>
      </div>
    );
  }

  return (
    <div className="max-w-3xl">
      <h2 className="text-2xl font-bold mb-6">Blue Jets Suppliers</h2>
      <div className="grid gap-3">
        {suppliers.map((sup) => (
          <div key={sup.supplier_id} className="rounded-lg bg-gray-800 border border-gray-700 overflow-hidden">
            <button
              onClick={() => toggle(sup)}
              className="w-full text-left p-4 hover:bg-gray-750"
            >
              <div className="flex justify-between items-start">
                <div>
                  <div className="font-medium">{sup.name}</div>
                  {sup.cage_code && (
                    <div className="text-xs text-gray-400">CAGE: {sup.cage_code}</div>
                  )}
                </div>
                <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                  sup.risk_rating === "LOW" ? "bg-green-900/50 text-green-300" :
                  sup.risk_rating === "MEDIUM" ? "bg-yellow-900/50 text-yellow-300" :
                  "bg-red-900/50 text-red-300"
                }`}>
                  {sup.risk_rating}
                </span>
              </div>
              {sup.quality_score && (
                <div className="mt-2 flex gap-4 text-xs text-gray-400">
                  <span>Quality: {(sup.quality_score * 100).toFixed(0)}%</span>
                </div>
              )}
            </button>

            {expanded === sup.supplier_id && (
              <div className="px-4 pb-4 border-t border-gray-700 pt-3">
                <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">
                  RFQs received
                </h4>
                {rfqsLoading && <div className="text-xs text-gray-500">Loading...</div>}
                {!rfqsLoading && rfqs?.length === 0 && (
                  <div className="text-xs text-gray-500">No RFQs recorded for this supplier yet</div>
                )}
                {rfqs?.map((entry) => (
                  <div
                    key={entry.negotiation_id}
                    className="mb-3 p-3 rounded-lg bg-gray-900/50 border border-gray-700/50"
                  >
                    <div className="flex items-center gap-2 text-xs mb-2">
                      <code className="text-gray-500">{entry.negotiation_id.slice(0, 8)}</code>
                      {entry.quadrant && (
                        <span className="px-1.5 py-0.5 rounded bg-gray-700 text-gray-300">
                          {entry.quadrant}
                        </span>
                      )}
                      {entry.status && (
                        <span className="px-1.5 py-0.5 rounded bg-blue-900/50 text-blue-300">
                          {entry.status}
                        </span>
                      )}
                    </div>

                    <div className="text-xs text-gray-400 mb-1">
                      <span className="text-gray-500">Invitations: </span>
                      {entry.invitations.length === 0
                        ? "none recorded"
                        : [...entry.invitations]
                            .sort((a, b) => ts(b.created_at) - ts(a.created_at))
                            .map((i) => `${i.type} (${formatTimestamp(i.created_at)})`)
                            .join(", ")}
                    </div>

                    <div className="text-xs text-gray-400 mb-1">
                      <span className="text-gray-500">Response: </span>
                      {entry.response ? (
                        <>
                          ${entry.response.amount?.toLocaleString()} {entry.response.currency}
                          {entry.response.delivery_days && ` — ${entry.response.delivery_days}d delivery`}
                          {entry.response.source && ` — ${entry.response.source}`}
                        </>
                      ) : (
                        "no response on record"
                      )}
                    </div>

                    <div className="text-xs text-gray-400">
                      <span className="text-gray-500">Feedback: </span>
                      {entry.feedback.length === 0 ? (
                        "none yet"
                      ) : (
                        <div className="mt-1 flex flex-col gap-1">
                          {[...entry.feedback].sort((a, b) => ts(b.created_at) - ts(a.created_at)).map((f) => (
                            <span
                              key={f.communication_id}
                              className={`px-1.5 py-0.5 rounded text-[10px] font-medium w-fit ${feedbackColor(f.type)}`}
                            >
                              {f.type}
                              {f.type === "AUCTION_ROUND_FEEDBACK" && f.current_rank != null &&
                                ` — rank ${f.current_rank}`}
                              {" "}({formatTimestamp(f.created_at)})
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
