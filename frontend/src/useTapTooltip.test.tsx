import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useTapTooltip } from "./useTapTooltip";

const onNavigate = vi.fn();

function Days() {
  const tooltip = useTapTooltip();
  return (
    <div>
      {["mon", "tue"].map((day) => (
        <a
          key={day}
          href={`/day/${day}`}
          {...tooltip.itemProps(day)}
          className={tooltip.openKey === day ? "is-open" : ""}
          onClickCapture={(e) => {
            // Record whether the click survived the hook (a real link would navigate).
            setTimeout(() => {
              if (!e.nativeEvent.defaultPrevented) onNavigate(day);
            });
          }}
        >
          {day}
        </a>
      ))}
      <p>elsewhere</p>
    </div>
  );
}

function tap(element: HTMLElement, pointerType: string) {
  fireEvent.pointerDown(element, { pointerType });
  fireEvent.click(element);
}

describe("useTapTooltip", () => {
  it("shows the tooltip on the first tap and follows the link on the second", async () => {
    onNavigate.mockClear();
    render(<Days />);
    const mon = screen.getByText("mon");
    tap(mon, "touch");
    expect(mon).toHaveClass("is-open");
    await new Promise((r) => setTimeout(r));
    expect(onNavigate).not.toHaveBeenCalled();

    tap(mon, "touch");
    await new Promise((r) => setTimeout(r));
    expect(onNavigate).toHaveBeenCalledWith("mon");
  });

  it("moves to another item, and closes on a tap elsewhere", () => {
    render(<Days />);
    tap(screen.getByText("mon"), "touch");
    tap(screen.getByText("tue"), "touch");
    expect(screen.getByText("tue")).toHaveClass("is-open");
    expect(screen.getByText("mon")).not.toHaveClass("is-open");

    fireEvent.pointerDown(screen.getByText("elsewhere"), { pointerType: "touch" });
    expect(screen.getByText("tue")).not.toHaveClass("is-open");
  });

  it("leaves mouse clicks alone", async () => {
    onNavigate.mockClear();
    render(<Days />);
    tap(screen.getByText("mon"), "mouse");
    expect(screen.getByText("mon")).not.toHaveClass("is-open");
    await new Promise((r) => setTimeout(r));
    expect(onNavigate).toHaveBeenCalledWith("mon");
  });
});
