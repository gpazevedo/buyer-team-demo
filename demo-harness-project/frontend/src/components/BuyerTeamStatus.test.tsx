import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import BuyerTeamStatus from "./BuyerTeamStatus";

function mockHealth(response: unknown) {
  return vi.fn(() => Promise.resolve({ json: () => Promise.resolve(response) }));
}

describe("BuyerTeamStatus", () => {
  beforeEach(() => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("shows a checking state before the first health check resolves", () => {
    vi.stubGlobal("fetch", mockHealth({ healthy: true, checks: {} }));

    render(<BuyerTeamStatus />);

    expect(screen.getByText("checking Buyer Team...")).toBeInTheDocument();
  });

  it("shows reachable + live pricing once healthy with an agent-priced bid", async () => {
    vi.stubGlobal(
      "fetch",
      mockHealth({
        healthy: true,
        checks: {
          approval_gate_lambda: "ok",
          master_store_table: "ok",
          requisitions_table: "ok",
          step_functions: "ok",
          agentcore_runtimes: "ok",
        },
        identity: { account: "234876310489", region: "us-east-1", env: "dev" },
        pricing_mode: "live",
        pricing_mode_source: "spot_bidding_agent",
      })
    );

    render(<BuyerTeamStatus />);

    expect(await screen.findByText("Buyer Team reachable")).toBeInTheDocument();
    expect(screen.getByText("LLM agents reachable")).toBeInTheDocument();
    expect(screen.getByText("Step Functions")).toBeInTheDocument();
  });

  it("names each AWS service it depends on", async () => {
    vi.stubGlobal(
      "fetch",
      mockHealth({
        healthy: true,
        checks: {
          approval_gate_lambda: "ok",
          master_store_table: "ok",
          requisitions_table: "ok",
          step_functions: "ok",
          agentcore_runtimes: "ok",
        },
      })
    );

    render(<BuyerTeamStatus />);

    expect(await screen.findByText("AWS Lambda")).toBeInTheDocument();
    expect(screen.getByText("DynamoDB")).toBeInTheDocument();
    expect(screen.getByText("Step Functions")).toBeInTheDocument();
    expect(screen.getByText("Bedrock AgentCore")).toBeInTheDocument();
  });

  it("shows the AWS account, region and env so the substrate is unambiguous", async () => {
    vi.stubGlobal(
      "fetch",
      mockHealth({
        healthy: true,
        checks: { step_functions: "ok" },
        identity: { account: "234876310489", region: "us-east-1", env: "dev" },
      })
    );

    render(<BuyerTeamStatus />);

    expect(
      await screen.findByText("AWS us-east-1 · dev · acct 234876310489")
    ).toBeInTheDocument();
  });

  it("omits a service with no reported check rather than showing it as broken", async () => {
    vi.stubGlobal(
      "fetch",
      mockHealth({ healthy: true, checks: { step_functions: "ok" } })
    );

    render(<BuyerTeamStatus />);

    expect(await screen.findByText("Step Functions")).toBeInTheDocument();
    expect(screen.queryByText("Bedrock AgentCore")).not.toBeInTheDocument();
    expect(screen.queryByText("AWS Lambda")).not.toBeInTheDocument();
  });

  it("marks a service red and surfaces the error when its check fails", async () => {
    vi.stubGlobal(
      "fetch",
      mockHealth({
        healthy: false,
        checks: { step_functions: "error: state machine not found" },
      })
    );

    render(<BuyerTeamStatus />);

    const badge = await screen.findByText("Step Functions");
    expect(badge.getAttribute("title")).toContain("state machine not found");
    expect(badge.querySelector(".bg-red-500")).not.toBeNull();
  });

  it("shows unreachable when the health check reports unhealthy", async () => {
    vi.stubGlobal(
      "fetch",
      mockHealth({ healthy: false, checks: { approval_gate_lambda: "error: ResourceNotFound" } })
    );

    render(<BuyerTeamStatus />);

    expect(await screen.findByText("Buyer Team unreachable")).toBeInTheDocument();
  });

  it("shows fallback pricing messaging", async () => {
    vi.stubGlobal(
      "fetch",
      mockHealth({
        healthy: true,
        checks: {},
        pricing_mode: "fallback",
        pricing_mode_source: "spot_fallback_stub",
      })
    );

    render(<BuyerTeamStatus />);

    expect(await screen.findByText("Fallback pricing (VPC/NAT down)")).toBeInTheDocument();
  });

  it("shows 'No recent bids' when pricing mode is unknown", async () => {
    vi.stubGlobal("fetch", mockHealth({ healthy: true, checks: {} }));

    render(<BuyerTeamStatus />);

    expect(await screen.findByText("No recent bids")).toBeInTheDocument();
  });

  it("marks unreachable when the health request itself fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("network down")))
    );

    render(<BuyerTeamStatus />);

    expect(await screen.findByText("Buyer Team unreachable")).toBeInTheDocument();
  });

  it("polls the health endpoint every 15s", async () => {
    vi.useFakeTimers();
    const fetchMock = mockHealth({ healthy: true, checks: {} });
    vi.stubGlobal("fetch", fetchMock);

    render(<BuyerTeamStatus />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const callsBefore = fetchMock.mock.calls.length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });

    expect(fetchMock.mock.calls.length).toBeGreaterThan(callsBefore);
  });
});
