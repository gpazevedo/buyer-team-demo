import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AuditTrail from "./AuditTrail";

describe("AuditTrail", () => {
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

  const nodeEvent = {
    ts: "2026-09-04T22:40:47.000Z",
    event_id: "e1",
    actor: "orchestrator.node.kraljic_classify",
    event_type: "node_decision",
    source_layer: "lambda_core",
    detail: { quadrant: "STRATEGIC" },
  };
  const agentEvent = {
    ts: "2026-09-04T22:41:18.000Z",
    event_id: "e2",
    actor: "agent:strategic-partnership-agent",
    event_type: "tool_call",
    source_layer: "buyer_agent_core",
    detail: { result: "ok" },
  };

  const respond = (body: unknown) =>
    fetchMock.mockResolvedValue({ ok: true, json: () => Promise.resolve(body) });

  it("shows the event count without expanding", async () => {
    respond({ count: 2, agent_tier_reached: true, events: [nodeEvent, agentEvent] });

    render(<AuditTrail negotiationId="n-1" />);

    expect(await screen.findByText("2 events")).toBeInTheDocument();
    // collapsed by default — detail JSON not rendered yet
    expect(screen.queryByText(/STRATEGIC/)).not.toBeInTheDocument();
  });

  it("names the AWS runtime behind each decision once expanded", async () => {
    respond({ count: 2, agent_tier_reached: true, events: [nodeEvent, agentEvent] });

    render(<AuditTrail negotiationId="n-1" />);
    await userEvent.click(await screen.findByText("Expand"));

    expect(screen.getByText("λ Lambda")).toBeInTheDocument();
    expect(screen.getByText("Bedrock AgentCore")).toBeInTheDocument();
  });

  it("strips the actor prefixes the layer badge already conveys", async () => {
    respond({ count: 2, agent_tier_reached: true, events: [nodeEvent, agentEvent] });

    render(<AuditTrail negotiationId="n-1" />);
    await userEvent.click(await screen.findByText("Expand"));

    expect(screen.getByText("kraljic_classify")).toBeInTheDocument();
    expect(screen.getByText("strategic-partnership-agent")).toBeInTheDocument();
  });

  it("says the agent tier did not run rather than looking broken", async () => {
    respond({ count: 1, agent_tier_reached: false, events: [nodeEvent] });

    render(<AuditTrail negotiationId="n-1" />);

    expect(await screen.findByText(/agent tier did not run/)).toBeInTheDocument();
  });

  it("does not show the fallback notice when agents did run", async () => {
    respond({ count: 2, agent_tier_reached: true, events: [nodeEvent, agentEvent] });

    render(<AuditTrail negotiationId="n-1" />);

    await screen.findByText("2 events");
    expect(screen.queryByText(/agent tier did not run/)).not.toBeInTheDocument();
  });

  it("handles a negotiation with no recorded events", async () => {
    respond({ count: 0, agent_tier_reached: false, events: [] });

    render(<AuditTrail negotiationId="n-1" />);

    expect(
      await screen.findByText("No decision events recorded yet for this negotiation.")
    ).toBeInTheDocument();
  });
});
