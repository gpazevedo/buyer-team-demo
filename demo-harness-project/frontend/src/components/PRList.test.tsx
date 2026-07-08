import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PRList from "./PRList";

const PR_NO_NEGOTIATION = {
  requisition_id: "req-1",
  status: "PENDING",
  created_at: "2026-07-08T10:00:00Z",
  items: [{ item_id: "i1", sku: "BJ-25-LAVKIT", name: "Lavatory kit", quantity: 2, unit_price: 180, total: 360 }],
};

const PR_WITH_NEGOTIATION = {
  requisition_id: "req-2",
  negotiation_id: "neg-2",
  status: "IN_PROGRESS",
  created_at: "2026-07-08T11:00:00Z",
  items: [{ item_id: "i2", sku: "BJ-32-MWTIRE", name: "Main wheel tire", quantity: 1, unit_price: 2400, total: 2400 }],
};

const NEGOTIATION_STATE = {
  status: "EVALUATING",
  quadrant: "LEVERAGE",
  strategy: "COMPETITIVE_AUCTION",
  approval_block_reason: null,
  bids: [
    {
      bid_id: "b1",
      supplier_id: "s1",
      supplier_name: "Acme Corp",
      amount: 2400,
      currency: "USD",
    },
  ],
  awards: [],
  orders: [],
};

function mockFetchSequence(prs: unknown[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url === "/demo/requisitions") {
        return Promise.resolve({ json: () => Promise.resolve(prs) });
      }
      if (url === "/demo/negotiations/neg-2") {
        return Promise.resolve({ json: () => Promise.resolve(NEGOTIATION_STATE) });
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`));
    })
  );
}

describe("PRList", () => {
  beforeEach(() => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows the empty state when there are no requisitions", async () => {
    mockFetchSequence([]);

    render(<PRList onSelectNegotiation={vi.fn()} />);

    expect(await screen.findByText("No purchase requisitions yet")).toBeInTheDocument();
  });

  it("renders each requisition with its status", async () => {
    mockFetchSequence([PR_NO_NEGOTIATION]);

    render(<PRList onSelectNegotiation={vi.fn()} />);

    expect(await screen.findByText("req-1")).toBeInTheDocument();
    expect(screen.getByText("PENDING")).toBeInTheDocument();
  });

  it("expands a requisition to show its line items", async () => {
    mockFetchSequence([PR_NO_NEGOTIATION]);
    const user = userEvent.setup();
    render(<PRList onSelectNegotiation={vi.fn()} />);

    await user.click(await screen.findByText("req-1"));

    expect(await screen.findByText("Lavatory kit")).toBeInTheDocument();
    expect(screen.getByText("BJ-25-LAVKIT")).toBeInTheDocument();
  });

  it("collapses a requisition when clicked a second time", async () => {
    mockFetchSequence([PR_NO_NEGOTIATION]);
    const user = userEvent.setup();
    render(<PRList onSelectNegotiation={vi.fn()} />);

    const row = await screen.findByText("req-1");
    await user.click(row);
    expect(await screen.findByText("Lavatory kit")).toBeInTheDocument();

    await user.click(row);
    await waitFor(() => expect(screen.queryByText("Lavatory kit")).not.toBeInTheDocument());
  });

  it("loads negotiation state and renders bids when a PR has a negotiation", async () => {
    mockFetchSequence([PR_WITH_NEGOTIATION]);
    const user = userEvent.setup();
    render(<PRList onSelectNegotiation={vi.fn()} />);

    await user.click(await screen.findByText("req-2"));

    expect(await screen.findByText("Acme Corp")).toBeInTheDocument();
    expect(screen.getByText("LEVERAGE")).toBeInTheDocument();
    expect(screen.getByText("EVALUATING")).toBeInTheDocument();
  });

  it("calls onSelectNegotiation when the Timeline link is clicked", async () => {
    mockFetchSequence([PR_WITH_NEGOTIATION]);
    const onSelectNegotiation = vi.fn();
    const user = userEvent.setup();
    render(<PRList onSelectNegotiation={onSelectNegotiation} />);

    await user.click(await screen.findByText("req-2"));
    await user.click(await screen.findByText("Open live Timeline →"));

    expect(onSelectNegotiation).toHaveBeenCalledWith("neg-2");
  });
});
