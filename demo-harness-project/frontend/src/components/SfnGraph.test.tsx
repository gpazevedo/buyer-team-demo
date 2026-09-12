import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SfnGraph from "./SfnGraph";

describe("SfnGraph", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  const graphResponse = {
    execution_status: "RUNNING",
    url: "https://example.com/sfn-console",
    states: [
      { name: "IngestValidate", status: "succeeded" },
      { name: "KraljicClassify", status: "succeeded" },
      { name: "RouteStrategy", status: "succeeded" },
      { name: "StrategyExecute", status: "running" },
      { name: "BidEvaluation", status: "pending" },
      { name: "ApprovalGate", status: "pending" },
      { name: "AwardComms", status: "pending" },
      { name: "Done", status: "pending" },
    ],
  };

  it("renders the executed graph with the execution status and console link", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: () => Promise.resolve(graphResponse) });

    render(<SfnGraph negotiationId="neg-1" />);

    expect(await screen.findByText("IngestValidate")).toBeInTheDocument();
    expect(screen.getByText("StrategyExecute")).toBeInTheDocument();
    expect(screen.getByText("Done")).toBeInTheDocument();
    expect(screen.getByText("RUNNING")).toBeInTheDocument();
    expect(screen.getByText("SFN Console ↗")).toHaveAttribute("href", "https://example.com/sfn-console");
  });

  it("polls the /sfn endpoint every 5s", async () => {
    vi.useFakeTimers();
    try {
      fetchMock.mockResolvedValue({ ok: true, json: () => Promise.resolve(graphResponse) });

      render(<SfnGraph negotiationId="neg-1" />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      const callsBefore = fetchMock.mock.calls.length;
      expect(fetchMock.mock.calls[0][0]).toBe("/demo/negotiations/neg-1/sfn");

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000);
      });

      expect(fetchMock.mock.calls.length).toBeGreaterThan(callsBefore);
    } finally {
      vi.useRealTimers();
    }
  });

  it("names the AWS primitive behind each state", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: () => Promise.resolve(graphResponse) });

    render(<SfnGraph negotiationId="neg-1" />);

    expect(await screen.findByText("λ node1-ingest-validate")).toBeInTheDocument();
    expect(screen.getByText("λ node5-bid-evaluation")).toBeInTheDocument();
    expect(screen.getByText("λ node6 · waitForTaskToken")).toBeInTheDocument();
    // The two agent-invoking nodes are the only ones that reach Bedrock.
    expect(screen.getAllByText("λ node2 → AgentCore")).toHaveLength(1);
    expect(screen.getAllByText("λ node3 → AgentCore")).toHaveLength(1);
  });

  it("does not label the pure Step Functions states as Lambdas", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: () => Promise.resolve(graphResponse) });

    render(<SfnGraph negotiationId="neg-1" />);

    // RouteStrategy is a Choice state and Done is a Succeed state — no Lambda
    // runs either one, so neither may carry a λ annotation.
    expect(await screen.findByText("SFN Choice")).toBeInTheDocument();
    expect(screen.getByText("SFN Succeed")).toBeInTheDocument();
    for (const name of ["RouteStrategy", "Done"]) {
      expect(screen.getByText(name).textContent).not.toContain("λ");
    }
  });

  it("shows a waiting placeholder until the execution exists", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 404 });

    render(<SfnGraph negotiationId="neg-1" />);

    expect(
      await screen.findByText("Waiting for the Step Functions execution to start...")
    ).toBeInTheDocument();
  });
});
