// Shared notes UI for both the calendar page (entity_type="day") and activity detail
// (entity_type="activity") -- see api/schemas/notes.py.
import { useState } from "react";

import { useCreateNote, useNotes } from "../api/queries";
import type { EntityType } from "../api/types";

export function NotesPanel({ entityType, entityId }: { entityType: EntityType; entityId: string }) {
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
      <h3>Notes</h3>
      {notes.isLoading && <p>Loading notes…</p>}
      {notes.isError && <p role="alert">Could not load notes.</p>}
      {notes.data && notes.data.length === 0 && <p>No notes yet.</p>}
      <ul>
        {notes.data?.map((note) => (
          <li key={note.id}>
            <p>{note.body}</p>
            <small>{new Date(note.created_at).toLocaleString()}</small>
          </li>
        ))}
      </ul>
      <form onSubmit={handleSubmit}>
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Add a note…"
        />
        <button type="submit" disabled={createNote.isPending}>
          {createNote.isPending ? "Saving…" : "Add note"}
        </button>
      </form>
    </section>
  );
}
