import { Suspense, useState } from "react";
import PRForm from "./components/PRForm";
import PRList from "./components/PRList";
import Timeline from "./components/Timeline";
import SupplierInbox from "./components/SupplierInbox";
import BuyerTeamStatus from "./components/BuyerTeamStatus";
import UtcClock from "./components/UtcClock";

type Tab = "pr" | "requisitions" | "timeline" | "suppliers";

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>(() => {
    const saved = sessionStorage.getItem("demo:activeTab");
    return (saved as Tab) || "pr";
  });
  const [activeNegotiationId, setActiveNegotiationId] = useState<string | null>(() => {
    return sessionStorage.getItem("demo:negotiationId") || null;
  });
  const [initialQuadrant, setInitialQuadrant] = useState<string | null>(null);

  const tabs: { id: Tab; label: string }[] = [
    { id: "pr", label: "New PR" },
    { id: "requisitions", label: "Requisitions" },
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
            <div className="mt-1">
              <BuyerTeamStatus />
            </div>
          </div>
          <div className="flex flex-col items-end gap-2">
            <UtcClock />
            <div className="flex gap-2">
              {tabs.map((t) => (
                <button
                  key={t.id}
                  onClick={() => {
                    console.log("[App] tab ->", t.id);
                    setActiveTab(t.id);
                  }}
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
        </div>
      </header>
      <main className="p-8">
        <Suspense fallback={<div className="text-gray-400">Loading...</div>}>
          {activeTab === "pr" && (
            <PRForm onCreated={(negId, quadrant) => {
              console.log("[App] PR created, switching to Timeline for negotiation", negId);
              setActiveNegotiationId(negId);
              setInitialQuadrant(quadrant);
              setActiveTab("timeline");
            }} />
          )}
          {activeTab === "requisitions" && (
            <PRList onSelectNegotiation={(negId) => {
              console.log("[App] PR selected, switching to Timeline for negotiation", negId);
              setActiveNegotiationId(negId);
              setActiveTab("timeline");
            }} />
          )}
          {activeTab === "timeline" && (
            <Timeline negotiationId={activeNegotiationId} initialQuadrant={initialQuadrant} />
          )}
          {activeTab === "suppliers" && <SupplierInbox />}
        </Suspense>
      </main>
    </div>
  );
}
