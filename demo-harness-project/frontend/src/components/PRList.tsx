import { useEffect, useState } from "react";
import OfferCard, { type Bid } from "./OfferCard";

type Item = {
  item_id: string;
  sku: string;
  name: string;
  quantity: number;
  unit_price: number;
  total: number;
};

type PR = {
  requisition_id: string;
  negotiation_id?: string;
  status: string;
  created_at: string;
  budget_override?: number;
  items: Item[];
};

type NegotiationState = {
  status: string | null;
  quadrant: string | null;
  strategy: string | null;
  approval_block_reason: string | null;
  bids: Bid[];
  awards: { award_id: string; supplier_name: string; total_amount: number }[];
  orders: { order_id: string; supplier_name: string; total_value: number }[];
};

export default function PRList({ onSelectNegotiation }: { onSelectNegotiation: (negId: string) => void }) {
  const [prs, setPrs] = useState<PR[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [negotiation, setNegotiation] = useState<NegotiationState | null>(null);
  const [negLoading, setNegLoading] = useState(false);

  useEffect(() => {
    fetch("/demo/requisitions")
      .then((r) => r.json())
      .then((data) => {
        console.log("[PRList] loaded requisitions", data.length);
        setPrs(data);
      })
      .catch((e) => console.error("[PRList] failed to load requisitions", e));
  }, []);

  function toggle(pr: PR) {
    console.log("[PRList] clicked PR", pr.requisition_id);
    if (expanded === pr.requisition_id) {
      setExpanded(null);
      setNegotiation(null);
      return;
    }
    setExpanded(pr.requisition_id);
    setNegotiation(null);
    if (!pr.negotiation_id) return;

    setNegLoading(true);
    fetch(`/demo/negotiations/${pr.negotiation_id}`)
      .then((r) => r.json())
      .then((data) => {
        console.log("[PRList] negotiation state for", pr.requisition_id, data);
        setNegotiation(data);
      })
      .catch((e) => console.error("[PRList] failed to load negotiation state", e))
      .finally(() => setNegLoading(false));
  }

  if (prs.length === 0) {
    return (
      <div className="text-center py-12 text-gray-400">
        <p>No purchase requisitions yet</p>
        <p className="text-sm mt-2">Create one on the New PR tab</p>
      </div>
    );
  }

  return (
    <div className="max-w-3xl">
      <h2 className="text-2xl font-bold mb-6">Purchase Requisitions</h2>
      <div className="space-y-2">
        {prs.map((pr) => (
          <div key={pr.requisition_id} className="rounded-lg bg-gray-800 border border-gray-700 overflow-hidden">
            <button
              onClick={() => toggle(pr)}
              className="w-full flex justify-between items-center px-4 py-3 text-left hover:bg-gray-750"
            >
              <div>
                <div className="text-sm font-medium">{pr.requisition_id}</div>
                <div className="text-xs text-gray-500 mt-0.5">
                  {pr.items.length} item{pr.items.length === 1 ? "" : "s"} — created{" "}
                  {new Date(pr.created_at).toLocaleString()}
                </div>
              </div>
              <span className="px-2 py-0.5 rounded text-xs font-medium bg-gray-700 text-gray-300">
                {pr.status}
              </span>
            </button>

            {expanded === pr.requisition_id && (
              <div className="px-4 pb-4 border-t border-gray-700">
                <table className="w-full mt-3 text-sm">
                  <thead>
                    <tr className="text-left text-xs text-gray-500">
                      <th className="pb-1">Product</th>
                      <th className="pb-1">SKU</th>
                      <th className="pb-1 text-right">Qty</th>
                      <th className="pb-1 text-right">Total</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pr.items.map((item) => (
                      <tr key={item.item_id} className="border-t border-gray-700/50">
                        <td className="py-1.5">{item.name}</td>
                        <td className="py-1.5 text-gray-400 font-mono text-xs">{item.sku}</td>
                        <td className="py-1.5 text-right">{item.quantity}</td>
                        <td className="py-1.5 text-right">${item.total?.toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                <div className="mt-4 pt-3 border-t border-gray-700/50">
                  <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">
                    Negotiation
                  </h4>
                  {negLoading && <div className="text-xs text-gray-500">Loading...</div>}
                  {!negLoading && !negotiation && (
                    <div className="text-xs text-gray-500">Not started yet</div>
                  )}
                  {negotiation && (
                    <div className="space-y-3">
                      <div className="flex items-center gap-2 text-xs">
                        {negotiation.quadrant && (
                          <span className="px-2 py-0.5 rounded bg-gray-700 text-gray-300">
                            {negotiation.quadrant}
                          </span>
                        )}
                        {negotiation.strategy && (
                          <span className="text-gray-400">{negotiation.strategy}</span>
                        )}
                        {negotiation.status && (
                          <span className="px-2 py-0.5 rounded bg-blue-900/50 text-blue-300">
                            {negotiation.status}
                          </span>
                        )}
                      </div>
                      {negotiation.approval_block_reason && (
                        <div className="text-xs text-yellow-400/80">
                          Approval blocked: {negotiation.approval_block_reason}
                        </div>
                      )}
                      {negotiation.bids?.length > 0 && (
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                          {negotiation.bids.map((bid) => (
                            <OfferCard key={bid.bid_id} bid={bid} />
                          ))}
                        </div>
                      )}
                      {negotiation.awards?.map((a) => (
                        <div key={a.award_id} className="text-xs text-green-400">
                          Awarded to {a.supplier_name} — ${a.total_amount?.toLocaleString()}
                        </div>
                      ))}
                      {negotiation.orders?.map((o) => (
                        <div key={o.order_id} className="text-xs text-green-400">
                          PO issued — ${o.total_value?.toLocaleString()}
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {pr.negotiation_id && (
                  <button
                    onClick={() => onSelectNegotiation(pr.negotiation_id!)}
                    className="mt-3 text-xs text-blue-400 hover:text-blue-300"
                  >
                    Open live Timeline →
                  </button>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
