import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { CoachOut } from "../api/types";
import { CoachCard } from "./CoachCard";
import { CoachBanner, CoacheeSwitcher } from "./CoachBar";

const mockSetCoach = vi.fn();
const mockStopSharing = vi.fn();
const mockStopCoaching = vi.fn();
const mockClear = vi.fn();
let coach: CoachOut;
let actingAs: { id: string; display_name: string } | null;
const mockViewAs = vi.fn();

const idle = { isPending: false, isError: false, error: null };

vi.mock("../api/queries", () => ({
  useCoach: () => ({ data: coach, isError: false }),
  useSetCoach: () => ({ mutate: mockSetCoach, ...idle }),
  useStopSharing: () => ({ mutate: mockStopSharing, ...idle }),
  useStopCoaching: () => ({ mutate: mockStopCoaching, ...idle }),
}));
vi.mock("../useActingAs", () => ({
  useActingAs: () => ({ actingAs, viewAs: mockViewAs }),
}));
vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ clear: mockClear }),
}));

const person = (id: string, name: string) => ({
  id,
  display_name: name,
  username: name.toLowerCase(),
  since: "2026-11-01T00:00:00Z",
});

beforeEach(() => {
  vi.clearAllMocks();
  actingAs = null;
  coach = { coach: null, coachees: [] };
});

describe("CoachCard", () => {
  it("shares with a coach chosen by username", () => {
    render(<CoachCard />);
    expect(screen.getByText("You have no coach.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Coach username"), { target: { value: " carter " } });
    fireEvent.click(screen.getByRole("button", { name: "Share with this coach" }));
    expect(mockSetCoach).toHaveBeenCalledWith("carter", expect.anything());
  });

  it("lets the athlete stop sharing", () => {
    coach = { coach: person("c1", "Carter"), coachees: [] };
    render(<CoachCard />);
    expect(screen.getByText("Carter")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Stop sharing" }));
    expect(mockStopSharing).toHaveBeenCalled();
  });

  it("lets a coach view as, or stop coaching, an athlete", () => {
    coach = { coach: null, coachees: [person("a1", "Alex")] };
    render(<CoachCard />);
    fireEvent.click(screen.getByRole("button", { name: "View as Alex" }));
    expect(mockViewAs).toHaveBeenCalledWith({ id: "a1", display_name: "Alex" });
    fireEvent.click(screen.getByRole("button", { name: "Stop coaching" }));
    expect(mockStopCoaching).toHaveBeenCalledWith("a1");
  });
});

describe("CoacheeSwitcher and CoachBanner", () => {
  it("shows nothing to someone who coaches nobody", () => {
    const { container } = render(<CoacheeSwitcher />);
    expect(container).toBeEmptyDOMElement();
    expect(render(<CoachBanner />).container).toBeEmptyDOMElement();
  });

  it("switches to a coachee and back", () => {
    coach = { coach: null, coachees: [person("a1", "Alex")] };
    render(<CoacheeSwitcher />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "a1" } });
    expect(mockClear).toHaveBeenCalled();
    expect(mockViewAs).toHaveBeenCalledWith({ id: "a1", display_name: "Alex" });
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "" } });
    expect(mockViewAs).toHaveBeenLastCalledWith(null);
  });

  it("says whose data is shown and returns to the coach's own view", () => {
    actingAs = { id: "a1", display_name: "Alex" };
    render(<CoachBanner />);
    expect(screen.getByRole("status")).toHaveTextContent("Viewing Alex as their coach");
    fireEvent.click(screen.getByRole("button", { name: "Back to my view" }));
    expect(mockViewAs).toHaveBeenCalledWith(null);
  });
});
