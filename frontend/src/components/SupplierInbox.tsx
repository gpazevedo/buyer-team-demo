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

export default function SupplierInbox() {
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);

  useEffect(() => {
    fetch("/demo/suppliers")
      .then((r) => r.json())
      .then(setSuppliers)
      .catch(() => {});
  }, []);

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
          <div key={sup.supplier_id} className="p-4 rounded-lg bg-gray-800 border border-gray-700">
            <div className="flex justify-between items-start">
              <div>
                <div className="font-medium">{sup.name}</div>
                {sup.cage_code && (
                  <div className="text-xs text-gray-400">CAGE: {sup.cage_code}</div>
                )}
              </div>
              <div className="text-right">
                <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                  sup.risk_rating === "LOW" ? "bg-green-900/50 text-green-300" :
                  sup.risk_rating === "MEDIUM" ? "bg-yellow-900/50 text-yellow-300" :
                  "bg-red-900/50 text-red-300"
                }`}>
                  {sup.risk_rating}
                </span>
              </div>
            </div>
            {sup.quality_score && (
              <div className="mt-2 flex gap-4 text-xs text-gray-400">
                <span>Quality: {(sup.quality_score * 100).toFixed(0)}%</span>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
