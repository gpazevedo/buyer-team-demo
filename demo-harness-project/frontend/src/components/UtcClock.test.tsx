import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import UtcClock from "./UtcClock";

describe("UtcClock", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the current time in UTC", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-08T18:32:07Z"));

    render(<UtcClock />);

    expect(screen.getByText("2026-07-08 18:32:07 UTC")).toBeInTheDocument();
  });

  it("ticks forward every second", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-08T18:32:07Z"));
    render(<UtcClock />);

    await act(async () => {
      vi.advanceTimersByTime(3000);
    });

    expect(screen.getByText("2026-07-08 18:32:10 UTC")).toBeInTheDocument();
  });
});
