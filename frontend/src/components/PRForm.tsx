import { useActionState, useEffect, useState } from "react";

const QUADRANTS = [
  { value: "NON_CRITICAL", label: "Non-Critical", desc: "SPOT_BID — Auto-approve", color: "green" },
  { value: "LEVERAGE", label: "Leverage", desc: "COMPETITIVE_AUCTION — Price gate at $10k", color: "blue" },
  { value: "BOTTLENECK", label: "Bottleneck", desc: "PARTNERSHIP_RISK — Always HITL", color: "amber" },
  { value: "STRATEGIC", label: "Strategic", desc: "PARTNERSHIP_VALUE — Always HITL", color: "red" },
];

type ItemPreview = {
  sku: string;
  name: string;
  ata: string;
  estimated_unit_price: number;
  lead_time_days: number;
};

type PRResult = {
  requisition_id: string;
  negotiation_id: string;
  quadrant: string;
  item: { sku: string; name: string; estimated_unit_price: number };
  created_at: string;
};

export default function PRForm({ onCreated }: { onCreated: (negId: string) => void }) {
  const [quadrant, setQuadrant] = useState("NON_CRITICAL");
  const [quantity, setQuantity] = useState(1);
  const [itemPreview, setItemPreview] = useState<ItemPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  useEffect(() => {
    setPreviewLoading(true);
    fetch(`/demo/items?quadrant=${quadrant}`)
      .then((r) => r.json())
      .then((data) => {
        setItemPreview(data);
        setPreviewLoading(false);
      })
      .catch(() => setPreviewLoading(false));
  }, [quadrant]);

  const [result, submitAction, isPending] = useActionState<PRResult | null, FormData>(
    async (_prev: PRResult | null, formData: FormData) => {
      console.log("[PRForm] submitting PR", { quadrant, quantity });
      const res = await fetch("/demo/requisitions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ quadrant, quantity }),
      });
      if (!res.ok) {
        const err = await res.text();
        console.error("[PRForm] PR creation failed", res.status, err);
        alert(`Error: ${err}`);
        return null;
      }
      const data = await res.json();
      console.log("[PRForm] PR created", data);
      onCreated(data.negotiation_id);
      return data;
    },
    null
  );

  return (
    <div className="max-w-2xl">
      <h2 className="text-2xl font-bold mb-6">Create Purchase Requisition</h2>
      <form action={submitAction} className="space-y-6">
        {/* Quadrant selector */}
        <fieldset>
          <legend className="text-sm font-medium text-gray-300 mb-3">
            Kraljic Quadrant
          </legend>
          <div className="grid grid-cols-2 gap-3">
            {QUADRANTS.map((q) => (
              <label
                key={q.value}
                className={`flex items-start gap-3 p-4 rounded-lg border cursor-pointer transition-colors
                  ${q.color === "green" ? "border-green-800 hover:border-green-600 bg-green-950/30" : ""}
                  ${q.color === "blue" ? "border-blue-800 hover:border-blue-600 bg-blue-950/30" : ""}
                  ${q.color === "amber" ? "border-amber-800 hover:border-amber-600 bg-amber-950/30" : ""}
                  ${q.color === "red" ? "border-red-800 hover:border-red-600 bg-red-950/30" : ""}
                `}
              >
                <input
                  type="radio"
                  name="quadrant"
                  value={q.value}
                  checked={quadrant === q.value}
                  onChange={(e) => setQuadrant(e.target.value)}
                  className="mt-1"
                />
                <div>
                  <div className="font-medium text-sm">{q.label}</div>
                  <div className="text-xs text-gray-400 mt-0.5">{q.desc}</div>
                </div>
              </label>
            ))}
          </div>
        </fieldset>

        {/* Item preview */}
        {previewLoading && (
          <div className="p-3 rounded-lg bg-gray-800/50 border border-gray-700 text-xs text-gray-400">
            Loading item info...
          </div>
        )}
        {itemPreview && !previewLoading && (
          <div className="p-3 rounded-lg bg-gray-800/50 border border-gray-700">
            <div className="text-xs text-gray-500 mb-1">Selected item</div>
            <div className="text-sm font-medium">{itemPreview.name}</div>
            <div className="text-xs text-gray-400 mt-1 space-y-0.5">
              <div>SKU: {itemPreview.sku} | ATA: {itemPreview.ata}</div>
              <div>Est. unit price: ${itemPreview.estimated_unit_price.toLocaleString()}</div>
              <div>Lead time: {itemPreview.lead_time_days} days</div>
              <div className="text-blue-400 font-medium">
                Est. total: ${(itemPreview.estimated_unit_price * quantity).toLocaleString()} (qty {quantity})
              </div>
            </div>
          </div>
        )}

        {/* Quantity */}
        <div>
          <label className="text-sm font-medium text-gray-300 block mb-1">
            Quantity
          </label>
          <input
            type="number"
            name="quantity"
            value={quantity}
            onChange={(e) => setQuantity(parseInt(e.target.value) || 1)}
            min={1}
            max={100}
            className="w-32 px-3 py-2 rounded-lg bg-gray-800 border border-gray-700 text-white text-sm"
          />
        </div>

        <button
          type="submit"
          disabled={isPending}
          className="px-6 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700
            text-white font-medium rounded-lg transition-colors text-sm"
        >
          {isPending ? "Submitting..." : "Submit PR"}
        </button>
      </form>

      {result && (
        <div className="mt-6 p-4 rounded-lg bg-gray-800 border border-gray-700">
          <div className="text-sm font-medium text-green-400">PR Created</div>
          <div className="text-xs text-gray-400 mt-1 space-y-0.5">
            <div>Requisition: {result.requisition_id}</div>
            <div>Item: {result.item.sku} — {result.item.name}</div>
            <div>Est. Unit Price: ${result.item.estimated_unit_price.toLocaleString()}</div>
          </div>
        </div>
      )}
    </div>
  );
}
