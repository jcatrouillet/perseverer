import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NotesPanel } from "./NotesPanel";

const mockUseNotes = vi.fn();

vi.mock("../api/queries", () => ({
  useNotes: (...args: unknown[]) => mockUseNotes(...args),
  useCreateNote: () => ({ mutate: vi.fn(), isPending: false }),
}));

describe("NotesPanel", () => {
  beforeEach(() => {
    mockUseNotes.mockReturnValue({ isLoading: false, isError: false, data: [] });
  });

  it("shows its own 'Notes' heading by default", () => {
    render(<NotesPanel entityType="activity" entityId="a1" />);
    expect(screen.getByRole("heading", { name: "Notes" })).toBeInTheDocument();
  });

  it("hides its own heading when the caller already provides one", () => {
    render(<NotesPanel entityType="activity" entityId="a1" showHeading={false} />);
    expect(screen.queryByRole("heading", { name: "Notes" })).not.toBeInTheDocument();
  });
});
