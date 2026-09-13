import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { NoteOut } from "../api/types";
import { NotesPanel } from "./NotesPanel";

const mockUseNotes = vi.fn();
const mockCreateMutate = vi.fn();
const mockUpdateMutate = vi.fn();
const mockDeleteMutate = vi.fn();

vi.mock("../api/queries", () => ({
  useNotes: (...args: unknown[]) => mockUseNotes(...args),
  useCreateNote: () => ({ mutate: mockCreateMutate, isPending: false }),
  useUpdateNote: () => ({ mutate: mockUpdateMutate, isPending: false }),
  useDeleteNote: () => ({ mutate: mockDeleteMutate, isPending: false }),
}));

const NOTE: NoteOut = {
  id: 7,
  entity_type: "week",
  entity_id: "2026-09-07",
  body: "Deload week before the half marathon.",
  author: null,
  created_at: "2026-09-07T12:00:00Z",
  updated_at: "2026-09-07T12:00:00Z",
};

describe("NotesPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
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

  it("shows Edit/Delete buttons for an existing note", () => {
    mockUseNotes.mockReturnValue({ isLoading: false, isError: false, data: [NOTE] });
    render(<NotesPanel entityType="week" entityId="2026-09-07" />);
    expect(screen.getByText(NOTE.body)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });

  it("clicking Edit reveals a textarea pre-filled with the note's body, and Save submits the edit", () => {
    mockUseNotes.mockReturnValue({ isLoading: false, isError: false, data: [NOTE] });
    render(<NotesPanel entityType="week" entityId="2026-09-07" />);

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const textarea = screen.getByDisplayValue(NOTE.body);
    fireEvent.change(textarea, { target: { value: "Edited: deload week, easy runs only." } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(mockUpdateMutate).toHaveBeenCalledWith(
      { noteId: 7, body: "Edited: deload week, easy runs only." },
      expect.anything(),
    );
  });

  it("clicking Cancel while editing discards the change and shows the original body again", () => {
    mockUseNotes.mockReturnValue({ isLoading: false, isError: false, data: [NOTE] });
    render(<NotesPanel entityType="week" entityId="2026-09-07" />);

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByDisplayValue(NOTE.body), { target: { value: "scratch this" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.getByText(NOTE.body)).toBeInTheDocument();
    expect(screen.queryByText("scratch this")).not.toBeInTheDocument();
    expect(mockUpdateMutate).not.toHaveBeenCalled();
  });

  it("clicking Delete calls the delete mutation with the note's id and entity", () => {
    mockUseNotes.mockReturnValue({ isLoading: false, isError: false, data: [NOTE] });
    render(<NotesPanel entityType="week" entityId="2026-09-07" />);

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    expect(mockDeleteMutate).toHaveBeenCalledWith({
      noteId: 7,
      entityType: "week",
      entityId: "2026-09-07",
    });
  });

  it("hides the new-note textarea by default, showing only a '+ Add note' button", () => {
    render(<NotesPanel entityType="week" entityId="2026-09-07" />);
    expect(screen.getByRole("button", { name: "+ Add note" })).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Add a note…")).not.toBeInTheDocument();
  });

  it("clicking '+ Add note' reveals the textarea, and submitting creates the note", () => {
    render(<NotesPanel entityType="week" entityId="2026-09-07" />);

    fireEvent.click(screen.getByRole("button", { name: "+ Add note" }));
    const textarea = screen.getByPlaceholderText("Add a note…");
    fireEvent.change(textarea, { target: { value: "New note text" } });
    fireEvent.click(screen.getByRole("button", { name: "Add note" }));

    expect(mockCreateMutate).toHaveBeenCalledWith(
      { entity_type: "week", entity_id: "2026-09-07", body: "New note text" },
      expect.anything(),
    );
  });

  it("clicking Cancel while adding hides the textarea again without creating anything", () => {
    render(<NotesPanel entityType="week" entityId="2026-09-07" />);

    fireEvent.click(screen.getByRole("button", { name: "+ Add note" }));
    fireEvent.change(screen.getByPlaceholderText("Add a note…"), {
      target: { value: "abandoned draft" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByPlaceholderText("Add a note…")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ Add note" })).toBeInTheDocument();
    expect(mockCreateMutate).not.toHaveBeenCalled();
  });

  it("supports Unicode input in the new-note textarea", () => {
    render(<NotesPanel entityType="week" entityId="2026-09-07" />);
    const unicodeBody = "Test emoji 🏃‍♂️ café 你好 💪";

    fireEvent.click(screen.getByRole("button", { name: "+ Add note" }));
    const textarea = screen.getByPlaceholderText("Add a note…") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: unicodeBody } });
    expect(textarea.value).toBe(unicodeBody);

    fireEvent.click(screen.getByRole("button", { name: "Add note" }));
    expect(mockCreateMutate).toHaveBeenCalledWith(
      { entity_type: "week", entity_id: "2026-09-07", body: unicodeBody },
      expect.anything(),
    );
  });
});
