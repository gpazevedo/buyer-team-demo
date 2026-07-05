import { useState, useTransition } from "react";

type Props = {
  requisitionId: string;
  blockReason: string | null;
};

export default function ApprovalControls({ requisitionId, blockReason }: Props) {
  const [result, setResult] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  async function handleDecision(decision: string, reason?: string) {
    startTransition(async () => {
      try {
        const res = await fetch(`/demo/negotiations/${requisitionId}/approve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision, reason }),
        });
        const data = await res.json();
        setResult(`${decision}: ${JSON.stringify(data)}`);
      } catch (e) {
        setResult(`Error: ${e}`);
      }
    });
  }

  return (
    <div className="p-4 rounded-lg bg-yellow-950/20 border border-yellow-800">
      <h3 className="font-semibold text-yellow-300 mb-2">Human Approval Required</h3>
      {blockReason && (
        <p className="text-xs text-yellow-400/70 mb-3">
          Reason: <code className="bg-yellow-900/30 px-1 rounded">{blockReason}</code>
        </p>
      )}
      <div className="flex gap-3">
        <button
          onClick={() => handleDecision("APPROVED")}
          disabled={isPending}
          className="px-4 py-2 bg-green-600 hover:bg-green-500 disabled:bg-gray-700
            text-white text-sm font-medium rounded-lg transition-colors"
        >
          Approve
        </button>
        <button
          onClick={() => handleDecision("CYCLE_BACK")}
          disabled={isPending}
          className="px-4 py-2 bg-amber-600 hover:bg-amber-500 disabled:bg-gray-700
            text-white text-sm font-medium rounded-lg transition-colors"
        >
          Cycle Back
        </button>
        <button
          onClick={() => handleDecision("REJECTED", "Demo-approver rejected")}
          disabled={isPending}
          className="px-4 py-2 bg-red-600 hover:bg-red-500 disabled:bg-gray-700
            text-white text-sm font-medium rounded-lg transition-colors"
        >
          Reject
        </button>
      </div>
      {result && (
        <div className="mt-3 text-xs text-gray-400 font-mono">{result}</div>
      )}
    </div>
  );
}
