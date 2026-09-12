import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AwsSignals from "./AwsSignals";

describe("AwsSignals", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  const response = {
    window_minutes: 60,
    metrics: {
      negotiations_started: {
        label: "Negotiations Started",
        value: 3,
        unit: "count",
        namespace: "procurement/business",
        dashboard_url: "https://example.com/domain",
      },
      negotiation_cost: {
        label: "Bedrock Cost",
        value: 1.234,
        unit: "usd",
        namespace: "procurement/cost",
        dashboard_url: "https://example.com/finops",
      },
      cycle_time: {
        label: "Cycle Time",
        value: null,
        unit: "s",
        namespace: "procurement/business",
        dashboard_url: "https://example.com/domain",
      },
    },
  };

  it("renders a tile per metric with its CloudWatch namespace", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: () => Promise.resolve(response) });

    render(<AwsSignals />);

    expect(await screen.findByText("Negotiations Started")).toBeInTheDocument();
    expect(screen.getByText("Bedrock Cost")).toBeInTheDocument();
    expect(screen.getAllByText("procurement/business")).toHaveLength(2);
    expect(screen.getByText("procurement/cost")).toBeInTheDocument();
  });

  it("attributes the numbers to CloudWatch and states the window", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: () => Promise.resolve(response) });

    render(<AwsSignals />);

    expect(await screen.findByText(/Amazon CloudWatch/)).toBeInTheDocument();
    expect(screen.getByText(/last 60m/)).toBeInTheDocument();
  });

  it("shows an unpublished metric as a dash, never as zero", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: () => Promise.resolve(response) });

    render(<AwsSignals />);

    expect(await screen.findByText("—")).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
    expect(screen.queryByText("0.0s")).not.toBeInTheDocument();
  });

  it("formats each unit for reading at a glance", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: () => Promise.resolve(response) });

    render(<AwsSignals />);

    expect(await screen.findByText("3")).toBeInTheDocument();
    expect(screen.getByText("$1.23")).toBeInTheDocument();
  });

  it("links each tile to the dashboard that owns the metric", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: () => Promise.resolve(response) });

    render(<AwsSignals />);

    const tile = (await screen.findByText("Bedrock Cost")).closest("a");
    expect(tile).toHaveAttribute("href", "https://example.com/finops");
  });

  it("reports plainly when CloudWatch is unavailable", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 503 });

    render(<AwsSignals />);

    expect(await screen.findByText("CloudWatch unavailable.")).toBeInTheDocument();
  });

  it("polls for fresh values while a negotiation runs", async () => {
    vi.useFakeTimers();
    try {
      fetchMock.mockResolvedValue({ ok: true, json: () => Promise.resolve(response) });

      render(<AwsSignals />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      const callsBefore = fetchMock.mock.calls.length;

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000);
      });

      expect(fetchMock.mock.calls.length).toBeGreaterThan(callsBefore);
    } finally {
      vi.useRealTimers();
    }
  });
});
