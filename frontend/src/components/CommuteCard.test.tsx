import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import type { CommuteSettingsOut } from "../api/types";
import { CommuteCard } from "./CommuteCard";

const mockUseCommute = vi.fn();
const mockMutate = vi.fn();
let saveState: { isPending: boolean; isSuccess: boolean; error: unknown } = {
  isPending: false,
  isSuccess: false,
  error: null,
};

vi.mock("../api/queries", () => ({
  useCommuteSettings: () => mockUseCommute(),
  useSetCommuteSettings: () => ({ mutate: mockMutate, ...saveState }),
}));

function settings(overrides: Partial<CommuteSettingsOut> = {}): CommuteSettingsOut {
  return {
    home_address: null,
    home_label: null,
    work_address: null,
    work_label: null,
    detection_active: false,
    detected_count: 0,
    radius_m: 400,
    ...overrides,
  };
}

describe("CommuteCard", () => {
  beforeEach(() => {
    mockMutate.mockClear();
    saveState = { isPending: false, isSuccess: false, error: null };
    mockUseCommute.mockReturnValue({ data: settings() });
  });

  it("says why it asks, that it is optional, and where the addresses go", () => {
    render(<CommuteCard />);
    expect(screen.getByRole("heading", { name: "Commute" })).toBeInTheDocument();
    expect(screen.getByText("Optional.")).toBeInTheDocument();
    expect(screen.getByText(/tag as a/)).toBeInTheDocument();
    expect(screen.getByText(/Leave both empty to skip it/)).toBeInTheDocument();
    expect(screen.getByText(/stay on your own server/)).toBeInTheDocument();
    expect(screen.getByText(/Nominatim search service/)).toBeInTheDocument();
    expect(screen.getByText(/always wins over the detection/)).toBeInTheDocument();
    expect(screen.getByText(/within 400 m/)).toBeInTheDocument();
  });

  it("mentions that Zwift activities are tagged virtual by themselves", () => {
    render(<CommuteCard />);
    expect(screen.getByText(/"Zwift" in the title are tagged as virtual/)).toBeInTheDocument();
  });

  it("saves the typed addresses, trimmed, and an empty one as null", () => {
    render(<CommuteCard />);
    fireEvent.change(screen.getByLabelText("Home address"), {
      target: { value: "  1 Home St, Seattle  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(mockMutate).toHaveBeenCalledWith({
      home_address: "1 Home St, Seattle",
      work_address: null,
    });
  });

  it("shows the saved addresses and what each was found as", () => {
    mockUseCommute.mockReturnValue({
      data: settings({
        home_address: "1 Home St, Seattle",
        home_label: "1 Home Street, Seattle, WA, USA",
        work_address: "2 Work Ave, Seattle",
        work_label: "2 Work Avenue, Seattle, WA, USA",
        detection_active: true,
        detected_count: 12,
      }),
    });
    render(<CommuteCard />);
    expect(screen.getByLabelText("Home address")).toHaveValue("1 Home St, Seattle");
    expect(screen.getByText("Found: 1 Home Street, Seattle, WA, USA")).toBeInTheDocument();
    expect(screen.getByText("Found: 2 Work Avenue, Seattle, WA, USA")).toBeInTheDocument();
    expect(
      screen.getByText(/Commute detection is on: 12 activities are tagged as a commute/),
    ).toBeInTheDocument();
  });

  it("drops the 'found' line once the address is edited", () => {
    mockUseCommute.mockReturnValue({
      data: settings({ home_address: "1 Home St", home_label: "1 Home Street, Seattle" }),
    });
    render(<CommuteCard />);
    fireEvent.change(screen.getByLabelText("Home address"), { target: { value: "9 Other Rd" } });
    expect(screen.queryByText(/Found:/)).not.toBeInTheDocument();
  });

  it("says detection is off until both addresses are set, and on with nothing matched yet", () => {
    const { unmount } = render(<CommuteCard />);
    expect(screen.getByText(/off until both addresses are set/)).toBeInTheDocument();
    unmount();
    mockUseCommute.mockReturnValue({ data: settings({ detection_active: true }) });
    render(<CommuteCard />);
    expect(
      screen.getByText(/Commute detection is on. No activity matches yet/),
    ).toBeInTheDocument();
  });

  it("shows the server's reason when an address can't be found", () => {
    saveState = {
      isPending: false,
      isSuccess: false,
      error: new ApiError(422, "{}", "Couldn't find the work address. Try adding the city."),
    };
    render(<CommuteCard />);
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't find the work address");
  });

  it("shows that it is looking the addresses up while saving", () => {
    saveState = { isPending: true, isSuccess: false, error: null };
    render(<CommuteCard />);
    expect(screen.getByRole("button", { name: "Looking up…" })).toBeDisabled();
  });
});
