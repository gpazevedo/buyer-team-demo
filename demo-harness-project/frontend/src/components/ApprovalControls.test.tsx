import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ApprovalControls from "./ApprovalControls";

function mockFetchResult(result: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve({ json: () => Promise.resolve({ result }) }))
  );
}

describe("ApprovalControls", () => {
  let reloadMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
    reloadMock = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, reload: reloadMock },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows the block reason when provided", () => {
    render(<ApprovalControls requisitionId="req-1" blockReason="price_over_ceiling" />);

    expect(screen.getByText("price_over_ceiling")).toBeInTheDocument();
  });

  it("omits the reason line when there is none", () => {
    render(<ApprovalControls requisitionId="req-1" blockReason={null} />);

    expect(screen.queryByText(/Reason:/)).not.toBeInTheDocument();
  });

  it("submits APPROVED and reloads on success", async () => {
    mockFetchResult({ status: "APPROVED" });
    const setItemSpy = vi.spyOn(Storage.prototype, "setItem");
    const user = userEvent.setup();
    render(<ApprovalControls requisitionId="req-1" blockReason={null} />);

    await user.click(screen.getByText("Approve"));

    expect(fetch).toHaveBeenCalledWith(
      "/demo/negotiations/req-1/approve",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ decision: "APPROVED", reason: undefined }),
      })
    );
    expect(setItemSpy).toHaveBeenCalledWith("demo:activeTab", "timeline");
    expect(reloadMock).toHaveBeenCalled();
  });

  it("submits REJECTED with a default reason", async () => {
    mockFetchResult({ status: "REJECTED" });
    const user = userEvent.setup();
    render(<ApprovalControls requisitionId="req-1" blockReason={null} />);

    await user.click(screen.getByText("Reject"));

    expect(fetch).toHaveBeenCalledWith(
      "/demo/negotiations/req-1/approve",
      expect.objectContaining({
        body: JSON.stringify({ decision: "REJECTED", reason: "Demo-approver rejected" }),
      })
    );
  });

  it("shows the error reason without reloading when the backend returns ERROR", async () => {
    mockFetchResult({ status: "ERROR", reason: "gate already released" });
    const user = userEvent.setup();
    render(<ApprovalControls requisitionId="req-1" blockReason={null} />);

    await user.click(screen.getByText("Cycle Back"));

    expect(await screen.findByText("gate already released")).toBeInTheDocument();
    expect(reloadMock).not.toHaveBeenCalled();
  });

  it("reloads immediately without touching sessionStorage when ALREADY_RESOLVED", async () => {
    mockFetchResult({ status: "ALREADY_RESOLVED" });
    const setItemSpy = vi.spyOn(Storage.prototype, "setItem");
    const user = userEvent.setup();
    render(<ApprovalControls requisitionId="req-1" blockReason={null} />);

    await user.click(screen.getByText("Approve"));

    expect(reloadMock).toHaveBeenCalled();
    expect(setItemSpy).not.toHaveBeenCalledWith("demo:activeTab", "timeline");
  });
});
