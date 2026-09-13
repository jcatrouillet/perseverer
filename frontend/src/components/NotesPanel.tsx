// Shared notes UI for the calendar day/week pages (entity_type="day"/"week") and activity
// detail (entity_type="activity") -- see api/schemas/notes.py.
import { useState } from "react";

import { useCreateNote, useDeleteNote, useNotes, useUpdateNote } from "../api/queries";
import type { EntityType, NoteOut } from "../api/types";
import "../styles/notes.css";

// One list item, own edit-mode state -- mirrors the Edit/Delete-in-place pattern
// PlannedRaceForm.tsx/ScheduleWorkoutForm.tsx already use for their own list rows (toggle to an
// inline form, Save/Cancel, no confirmation dialog on Delete -- this codebase doesn't use
// window.confirm anywhere, a mutation just fires immediately).
function NoteItem({
  note,
  entityType,
  entityId,
}: {
  note: NoteOut;
  entityType: EntityType;
  entityId: string;
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [body, setBody] = useState(note.body);
  const updateNote = useUpdateNote();
  const deleteNote = useDeleteNote();

  function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!body.trim()) return;
    updateNote.mutate(
      { noteId: note.id, body: body.trim() },
      { onSuccess: () => setIsEditing(false) },
    );
  }

  if (isEditing) {
    return (
      <li className="notes__item">
        <form className="notes__form" onSubmit={handleSave}>
          <textarea
            className="input notes__textarea"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            autoFocus
          />
          <div className="notes__item-actions">
            <button
              type="submit"
              className="button button--primary"
              disabled={updateNote.isPending || !body.trim()}
            >
              {updateNote.isPending ? "Saving…" : "Save"}
            </button>
            <button
              type="button"
              className="button"
              onClick={() => {
                setBody(note.body);
                setIsEditing(false);
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      </li>
    );
  }

  return (
    <li className="notes__item">
      <p className="notes__item-body">{note.body}</p>
      <div className="notes__item-footer">
        <small className="notes__item-meta">{new Date(note.created_at).toLocaleString()}</small>
        <div className="notes__item-actions">
          <button type="button" className="button" onClick={() => setIsEditing(true)}>
            Edit
          </button>
          <button
            type="button"
            className="button"
            onClick={() => deleteNote.mutate({ noteId: note.id, entityType, entityId })}
            disabled={deleteNote.isPending}
          >
            Delete
          </button>
        </div>
      </div>
    </li>
  );
}

export function NotesPanel({
  entityType,
  entityId,
  showHeading = true,
}: {
  entityType: EntityType;
  entityId: string;
  // ActivityDetailPage/DayViewPage/WeekView already wrap this in their own "Notes" <h2> card
  // heading -- rendering this component's own heading too would just duplicate the title.
  // MonthView's own expanded-day card heading is the date itself, not "Notes", so it relies on
  // the default to still show a "Notes" label.
  showHeading?: boolean;
}) {
  const notes = useNotes(entityType, entityId);
  const createNote = useCreateNote();
  const [isAdding, setIsAdding] = useState(false);
  const [body, setBody] = useState("");

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!body.trim()) return;
    createNote.mutate(
      { entity_type: entityType, entity_id: entityId, body: body.trim() },
      {
        onSuccess: () => {
          setBody("");
          setIsAdding(false);
        },
      },
    );
  }

  return (
    <section>
      {showHeading && <h3>Notes</h3>}
      {notes.isLoading && <p className="notes__status">Loading notes…</p>}
      {notes.isError && (
        <p className="notes__status" role="alert">
          Could not load notes.
        </p>
      )}
      {notes.data && notes.data.length === 0 && !isAdding && (
        <p className="notes__status">No notes yet.</p>
      )}
      {notes.data && notes.data.length > 0 && (
        <ul className="notes__list">
          {notes.data.map((note) => (
            <NoteItem key={note.id} note={note} entityType={entityType} entityId={entityId} />
          ))}
        </ul>
      )}
      {isAdding ? (
        <form className="notes__form" onSubmit={handleSubmit}>
          <textarea
            className="input notes__textarea"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Add a note…"
            autoFocus
          />
          <div className="notes__item-actions">
            <button
              type="submit"
              className="button button--primary"
              disabled={createNote.isPending || !body.trim()}
            >
              {createNote.isPending ? "Saving…" : "Add note"}
            </button>
            <button
              type="button"
              className="button"
              onClick={() => {
                setBody("");
                setIsAdding(false);
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <button type="button" className="button" onClick={() => setIsAdding(true)}>
          + Add note
        </button>
      )}
    </section>
  );
}
