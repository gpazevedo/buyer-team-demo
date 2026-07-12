import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Timeline from "./Timeline";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onerror: ((e: unknown) => void) | null = null;
  closed = false;
  private listeners: Record<string, Array<(e: { data: string }) => void>> = {};

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, cb: (e: { data: string }) => void) {
    (this.listeners[type] ||= []).push(cb);
  }

  close() {
    this.closed = true;
  }

  emit(type: string, payload: unknown) {
    for (const cb of this.listeners[type] || []) {
      cb({ data: JSON.stringify(payload) });
    }
  }
}

const baseSnapshot = {
  negotiation_id: "neg-1",
  requisition_id: "req-1",
  status: "IN_PROGRESS",
  quadrant: "LEVERAGE",
  strategy: "COMPETITIVE_AUCTION",
  approval_block_reason: null as string | null,
  total_cost_usd: null as number | null,
  invitations: [] as unknown[],
  bids: [] as unknown[],
  awards: [] as unknown[],
  orders: [] as unknown[],
};

describe("Timeline", () => {
  let snapshot: typeof baseSnapshot;
  let traces: { sfn: string | null; xray: string | null; cost_dashboard: string | null };
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);

    snapshot = { ...baseSnapshot };
    traces = { sfn: null, xray: null, cost_dashboard: "https://example.com/cost-dashboard" };

    fetchMock = vi.fn((url: string) => {
      if (url.endsWith("/traces")) {
        return Promise.resolve({ json: () => Promise.resolve(traces) });
      }
      return Promise.resolve({ json: () => Promise.resolve(snapshot) });
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows a placeholder when no negotiation is selected", () => {
    render(<Timeline negotiationId={null} initialQuadrant={null} />);
    expect(screen.getByText("No negotiation selected")).toBeInTheDocument();
  });

  it("renders the initial snapshot", async () => {
    render(<Timeline negotiationId="neg-1" initialQuadrant={null} />);

    // Quadrant/strategy render both in the header and the classification section.
    expect((await screen.findAllByText("LEVERAGE")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("COMPETITIVE_AUCTION").length).toBeGreaterThan(0);
    expect(screen.getByText("IN_PROGRESS")).toBeInTheDocument();
  });

  it("refetches the full snapshot on every SSE update event (no granular patching)", async () => {
    render(<Timeline negotiationId="neg-1" initialQuadrant={null} />);
    await screen.findByText("IN_PROGRESS");

    snapshot = {
      ...snapshot,
      status: "PENDING_APPROVAL",
      approval_block_reason: "price_over_ceiling",
    };
    await act(async () => {
      FakeEventSource.instances[0].emit("update", {
        event: "status_change",
        status: "PENDING_APPROVAL",
      });
    });

    expect(await screen.findByText("PENDING_APPROVAL")).toBeInTheDocument();
    expect(await screen.findByText("Human Approval Required")).toBeInTheDocument();
    expect(await screen.findByText(/price_over_ceiling/)).toBeInTheDocument();
  });

  it("stops polling for trace URLs once both SFN and X-Ray links are found", async () => {
    vi.useFakeTimers();
    try {
      traces = { sfn: "https://example.com/sfn", xray: null, cost_dashboard: "https://example.com/cost-dashboard" };
      render(<Timeline negotiationId="neg-1" initialQuadrant={null} />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      expect(screen.getByText("SFN Trace ↗")).toBeInTheDocument();
      const tracesCallsAfterFirst = fetchMock.mock.calls.filter((c) =>
        String(c[0]).endsWith("/traces")
      ).length;

      traces = {
        sfn: "https://example.com/sfn",
        xray: "https://example.com/xray",
        cost_dashboard: "https://example.com/cost-dashboard",
      };
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });
      expect(screen.getByText("X-Ray Trace ↗")).toBeInTheDocument();
      const tracesCallsAfterSecond = fetchMock.mock.calls.filter((c) =>
        String(c[0]).endsWith("/traces")
      ).length;
      expect(tracesCallsAfterSecond).toBe(tracesCallsAfterFirst + 1);

      // Both trace URLs are resolved now — the poll interval must have been
      // cleared, so no further /traces calls should happen no matter how
      // much more time passes.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });
      const tracesCallsAfterMore = fetchMock.mock.calls.filter((c) =>
        String(c[0]).endsWith("/traces")
      ).length;
      expect(tracesCallsAfterMore).toBe(tracesCallsAfterSecond);
    } finally {
      vi.useRealTimers();
    }
  });

  it("polls the snapshot every 5s as a fallback for missed SSE messages", async () => {
    vi.useFakeTimers();
    try {
      render(<Timeline negotiationId="neg-1" initialQuadrant={null} />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      const snapshotUrl = "/demo/negotiations/neg-1";
      const callsBefore = fetchMock.mock.calls.filter((c) => c[0] === snapshotUrl).length;

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000);
      });

      const callsAfter = fetchMock.mock.calls.filter((c) => c[0] === snapshotUrl).length;
      expect(callsAfter).toBeGreaterThan(callsBefore);
    } finally {
      vi.useRealTimers();
    }
  });

  it("renders supplier offers and the award once present in the snapshot", async () => {
    snapshot = {
      ...snapshot,
      bids: [
        {
          bid_id: "b1",
          supplier_id: "s1",
          supplier_name: "Acme Corp",
          amount: 2400,
          currency: "USD",
        },
      ],
      awards: [
        { award_id: "a1", supplier_name: "Acme Corp", total_amount: 2400, savings_amount: 100 },
      ],
    };

    render(<Timeline negotiationId="neg-1" initialQuadrant={null} />);

    // "Acme Corp" renders both in the offer card and the award section.
    expect((await screen.findAllByText("Acme Corp")).length).toBe(2);
    expect(screen.getByText("AWARDED")).toBeInTheDocument();
  });

  it("shows a non-clickable placeholder before the cost dashboard URL resolves", async () => {
    traces = { sfn: null, xray: null, cost_dashboard: null };

    render(<Timeline negotiationId="neg-1" initialQuadrant={null} />);

    const badge = await screen.findByText("📊");
    expect(badge.closest("a")).toBeNull();
    expect(screen.queryByText(/Est\. Cost/)).not.toBeInTheDocument();
  });

  it("links the dashboard emoji to the Cost Dashboard before any agent call has priced tokens", async () => {
    render(<Timeline negotiationId="neg-1" initialQuadrant={null} />);

    const link = await screen.findByText("📊 Dashboard ↗");
    expect(link.closest("a")).toHaveAttribute("href", "https://example.com/cost-dashboard");
    expect(screen.queryByText(/Est\. Cost/)).not.toBeInTheDocument();
  });

  it("shows the estimated cost as a link to the Cost Dashboard once available", async () => {
    snapshot = { ...snapshot, total_cost_usd: 2.1445 };

    render(<Timeline negotiationId="neg-1" initialQuadrant={null} />);

    const link = await screen.findByText("Est. Cost: $2.14 ↗");
    expect(link.closest("a")).toHaveAttribute("href", "https://example.com/cost-dashboard");
  });
});
