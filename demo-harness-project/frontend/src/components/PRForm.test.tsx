import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PRForm from "./PRForm";

const ITEM_PREVIEWS: Record<string, unknown> = {
  NON_CRITICAL: {
    sku: "BJ-25-LAVKIT",
    name: "Lavatory service consumable kit",
    ata: "38-10",
    estimated_unit_price: 180,
    lead_time_days: 7,
  },
  LEVERAGE: {
    sku: "BJ-32-MWTIRE",
    name: "Main wheel tire, radial",
    ata: "32-45",
    estimated_unit_price: 2400,
    lead_time_days: 14,
  },
};

function mockFetch({ onSubmit }: { onSubmit?: () => unknown } = {}) {
  return vi.fn((url: string, init?: RequestInit) => {
    if (url.startsWith("/demo/items")) {
      const quadrant = new URL(url, "http://localhost").searchParams.get("quadrant") || "NON_CRITICAL";
      return Promise.resolve({ json: () => Promise.resolve(ITEM_PREVIEWS[quadrant]) });
    }
    if (url === "/demo/requisitions" && init?.method === "POST") {
      const result = onSubmit?.() ?? { ok: true, body: { requisition_id: "req-1", negotiation_id: "neg-1", item: ITEM_PREVIEWS.NON_CRITICAL } };
      return Promise.resolve(result as any);
    }
    return Promise.reject(new Error(`unexpected fetch: ${url}`));
  });
}

describe("PRForm", () => {
  beforeEach(() => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(window, "alert").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("loads the item preview for the default quadrant", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<PRForm onCreated={vi.fn()} />);

    expect(await screen.findByText("Lavatory service consumable kit")).toBeInTheDocument();
    expect(screen.getByText("Est. total: $180 (qty 1)")).toBeInTheDocument();
  });

  it("reloads the preview when a different quadrant is selected", async () => {
    vi.stubGlobal("fetch", mockFetch());
    const user = userEvent.setup();
    render(<PRForm onCreated={vi.fn()} />);
    await screen.findByText("Lavatory service consumable kit");

    await user.click(screen.getByText("Leverage"));

    expect(await screen.findByText("Main wheel tire, radial")).toBeInTheDocument();
  });

  it("recomputes the estimated total client-side when quantity changes", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<PRForm onCreated={vi.fn()} />);
    await screen.findByText("Lavatory service consumable kit");

    // The Quantity <label> has no htmlFor, so it isn't programmatically
    // associated with the input — query by role instead of label text.
    // fireEvent.change sets the whole value in one shot; typing char-by-char
    // would hit the component's `parseInt(...) || 1` fallback on the
    // intermediate empty value and reset to 1 mid-keystroke.
    const quantityInput = screen.getByRole("spinbutton") as HTMLInputElement;
    fireEvent.change(quantityInput, { target: { value: "3" } });

    expect(await screen.findByText("Est. total: $540 (qty 3)")).toBeInTheDocument();
  });

  it("submits the PR and reports the created negotiation", async () => {
    const onCreated = vi.fn();
    vi.stubGlobal(
      "fetch",
      mockFetch({
        onSubmit: () => ({
          ok: true,
          json: () =>
            Promise.resolve({
              requisition_id: "req-1",
              negotiation_id: "neg-1",
              item: { sku: "BJ-25-LAVKIT", name: "Lavatory service consumable kit", estimated_unit_price: 180 },
            }),
        }),
      })
    );
    const user = userEvent.setup();
    render(<PRForm onCreated={onCreated} />);
    await screen.findByText("Lavatory service consumable kit");

    await user.click(screen.getByText("Submit PR"));

    expect(await screen.findByText("PR Created")).toBeInTheDocument();
    expect(onCreated).toHaveBeenCalledWith("neg-1", "NON_CRITICAL");
  });

  it("alerts on a failed submission and does not call onCreated", async () => {
    const onCreated = vi.fn();
    vi.stubGlobal(
      "fetch",
      mockFetch({
        onSubmit: () => ({ ok: false, status: 400, text: () => Promise.resolve("Unknown quadrant") }),
      })
    );
    const user = userEvent.setup();
    render(<PRForm onCreated={onCreated} />);
    await screen.findByText("Lavatory service consumable kit");

    await user.click(screen.getByText("Submit PR"));

    await vi.waitFor(() => expect(window.alert).toHaveBeenCalledWith("Error: Unknown quadrant"));
    expect(onCreated).not.toHaveBeenCalled();
    expect(screen.queryByText("PR Created")).not.toBeInTheDocument();
  });
});
