export type Bid = {
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
  priced_at?: number;
  created_at?: number;
};

function formatEpoch(seconds?: number): string | null {
  if (!seconds) return null;
  return new Date(seconds * 1000).toLocaleTimeString();
}

export default function OfferCard({ bid }: { bid: Bid }) {
  const isPriced = !!bid.amount;
  const isBest = bid.evaluation_rank === 1;

  return (
    <div className={`p-2.5 rounded-lg border transition-colors ${
      isBest ? "border-blue-700 bg-blue-950/20" : "border-gray-700 bg-gray-800"
    }`}>
      <div className="flex justify-between items-start mb-1">
        <div>
          <div className="font-medium text-sm">{bid.supplier_name || bid.supplier_id}</div>
          {bid.delivery_days && (
            <div className="text-xs text-gray-400">{bid.delivery_days} days delivery</div>
          )}
        </div>
        <div className="text-right">
          {isPriced ? (
            <div className="font-mono font-bold text-sm">
              ${bid.amount.toLocaleString()}
            </div>
          ) : (
            <div className="text-xs text-gray-500 italic">Awaiting price...</div>
          )}
          {bid.unit_price && (
            <div className="text-xs text-gray-400">
              ${bid.unit_price.toLocaleString()} / unit
            </div>
          )}
        </div>
      </div>

      {/* Source badge + status, single row */}
      <div className="flex items-center gap-2 mt-1">
        {bid.source && (
          <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
            bid.source.includes("agent") ? "bg-purple-900/50 text-purple-300" :
            bid.source.includes("fallback") ? "bg-red-900/50 text-red-300" :
            bid.source.includes("clamp") ? "bg-yellow-900/50 text-yellow-300" :
            "bg-gray-700 text-gray-400"
          }`}>
            {bid.source}
          </span>
        )}
        {isBest && (
          <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-blue-900/50 text-blue-300">
            BEST
          </span>
        )}
        <span className="text-[10px] text-gray-500 ml-auto">
          {bid.status || "UNKNOWN"}
          {formatEpoch(bid.priced_at) && <> · {formatEpoch(bid.priced_at)}</>}
        </span>
      </div>
    </div>
  );
}
