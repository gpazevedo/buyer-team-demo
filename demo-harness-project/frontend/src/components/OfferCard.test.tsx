import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import OfferCard from "./OfferCard";

const baseBid = {
  bid_id: "bid-1",
  supplier_id: "sup-1",
  supplier_name: "Acme Corp",
  amount: 2400,
  currency: "USD",
};

describe("OfferCard", () => {
  it("shows the awaiting-price state when there is no amount yet", () => {
    render(<OfferCard bid={{ ...baseBid, amount: 0 }} />);

    expect(screen.getByText("Awaiting price...")).toBeInTheDocument();
  });

  it("renders the formatted amount once priced", () => {
    render(<OfferCard bid={baseBid} />);

    expect(screen.getByText("$2,400")).toBeInTheDocument();
    expect(screen.queryByText("Awaiting price...")).not.toBeInTheDocument();
  });

  it("shows the BEST badge only for evaluation_rank 1", () => {
    const { rerender } = render(
      <OfferCard bid={{ ...baseBid, source: "spot_bidding_agent", evaluation_rank: 1 }} />
    );
    expect(screen.getByText("BEST")).toBeInTheDocument();

    rerender(<OfferCard bid={{ ...baseBid, source: "spot_bidding_agent", evaluation_rank: 2 }} />);
    expect(screen.queryByText("BEST")).not.toBeInTheDocument();
  });

  it("falls back to supplier_id when supplier_name is missing", () => {
    render(<OfferCard bid={{ ...baseBid, supplier_name: "", supplier_id: "sup-42" }} />);

    expect(screen.getByText("sup-42")).toBeInTheDocument();
  });

  it("renders delivery days only when present", () => {
    const { rerender } = render(<OfferCard bid={{ ...baseBid, delivery_days: 14 }} />);
    expect(screen.getByText("14 days delivery")).toBeInTheDocument();

    rerender(<OfferCard bid={{ ...baseBid, delivery_days: undefined }} />);
    expect(screen.queryByText(/days delivery/)).not.toBeInTheDocument();
  });
});
