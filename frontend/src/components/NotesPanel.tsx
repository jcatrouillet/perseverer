// Shared notes UI for both the calendar page (entity_type="day") and activity detail
// (entity_type="activity") -- see api/schemas/notes.py.
import { useState } from "react";

import { useCreateNote, useNotes } from "../api/queries";
import type { EntityType } from "../api/types";
import "../styles/notes.css";

export function NotesPanel({
  entityType,
  entityId,
  showHeading = true,
}: {
  entityType: EntityType;
  entityId: string;
  // ActivityDetailPage/DayViewPage already wrap this in their own "Notes" <h2> card heading --
  // rendering this component's own heading too would just duplicate the title. MonthView/
  // WeekView have no such wrapper (MonthView's own heading is the expanded date, WeekView has
  // none at all), so they rely on the default to still show a "Notes" label.
  showHeading?: boolean;
}) {
  const notes = useNotes(entityType, entityId);
  const createNote = useCreateNote();
  const [body, setBody] = useState("");

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!body.trim()) return;
    createNote.mutate(
      { entity_type: entityType, entity_id: entityId, body: body.trim() },
      { onSuccess: () => setBody("") },
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
      {notes.data && notes.data.length === 0 && <p className="notes__status">No notes yet.</p>}
      {notes.data && notes.data.length > 0 && (
        <ul className="notes__list">
          {notes.data.map((note) => (
            <li key={note.id} className="notes__item">
              <p className="notes__item-body">{note.body}</p>
              <small className="notes__item-meta">
                {new Date(note.created_at).toLocaleString()}
              </small>
            </li>
          ))}
        </ul>
      )}
      <form className="notes__form" onSubmit={handleSubmit}>
        <textarea
          className="input notes__textarea"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Add a note…"
        />
        <button
          type="submit"
          className="button button--primary notes__submit"
          disabled={createNote.isPending || !body.trim()}
        >
          {createNote.isPending ? "Saving…" : "Add note"}
        </button>
      </form>
    </section>
  );
}
