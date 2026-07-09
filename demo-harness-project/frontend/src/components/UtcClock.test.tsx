import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import UtcClock from "./UtcClock";

// Local time + its own UTC offset, whatever timezone this runs in — the
// clock is a local-time display, not a fixed-UTC one, so the expectation
// must be derived from the same local Date fields, not hardcoded to a
// specific offset.
function expectedLabel(date: Date): string {
  const pad = (n: number) => n.toString().padStart(2, "0");
  const offsetHours = -date.getTimezoneOffset() / 60;
  const sign = offsetHours >= 0 ? "+" : "-";
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())} ` +
    `UTC${sign}${Math.abs(offsetHours)}`
  );
}

describe("UtcClock", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the current local time with its UTC offset", () => {
    vi.useFakeTimers();
    const fixed = new Date(2026, 6, 8, 18, 32, 7); // local components, not UTC
    vi.setSystemTime(fixed);

    render(<UtcClock />);

    expect(screen.getByText(expectedLabel(fixed))).toBeInTheDocument();
  });

  it("ticks forward every second", async () => {
    vi.useFakeTimers();
    const fixed = new Date(2026, 6, 8, 18, 32, 7);
    vi.setSystemTime(fixed);
    render(<UtcClock />);

    await act(async () => {
      vi.advanceTimersByTime(3000);
    });

    expect(screen.getByText(expectedLabel(new Date(2026, 6, 8, 18, 32, 10)))).toBeInTheDocument();
  });
});
