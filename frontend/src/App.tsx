import { Suspense, useState } from "react";
import PRForm from "./components/PRForm";
import Timeline from "./components/Timeline";
import SupplierInbox from "./components/SupplierInbox";

type Tab = "pr" | "timeline" | "suppliers";

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>("pr");
  const [activeNegotiationId, setActiveNegotiationId] = useState<string | null>(null);

  const tabs: { id: Tab; label: string }[] = [
    { id: "pr", label: "New PR" },
    { id: "timeline", label: "Timeline" },
    { id: "suppliers", label: "Suppliers" },
  ];

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <header className="border-b border-gray-800 px-8 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold tracking-tight">Buyer Team</h1>
            <p className="text-sm text-gray-400">Lifecycle Demo — Blue Jets</p>
          </div>
          <div className="flex gap-2">
            {tabs.map((t) => (
              <button
                key={t.id}
                onClick={() => setActiveTab(t.id)}
                className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                  activeTab === t.id
                    ? "bg-blue-600 text-white"
                    : "bg-gray-800 text-gray-300 hover:bg-gray-700"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>
      </header>
      <main className="p-8">
        <Suspense fallback={<div className="text-gray-400">Loading...</div>}>
          {activeTab === "pr" && (
            <PRForm onCreated={(negId) => {
              setActiveNegotiationId(negId);
              setActiveTab("timeline");
            }} />
          )}
          {activeTab === "timeline" && (
            <Timeline negotiationId={activeNegotiationId} />
          )}
          {activeTab === "suppliers" && <SupplierInbox />}
        </Suspense>
      </main>
    </div>
  );
}
