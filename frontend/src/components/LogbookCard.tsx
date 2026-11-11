// Activity page, HIIT / strength only: the athlete's handwritten set notes. Photos of the notes are
// attached to the activity; the handwriting can be read into a list of exercises and sets (with
// Claude, when the server has a key); the athlete corrects that list, matches each exercise to the
// exercise library and puts the exercises in the order they were done, helped by the sets the
// watch recorded. The saved list is the activity's logbook and feeds the muscles-worked card.
import { useEffect, useMemo, useState } from "react";

import {
  useActivityExerciseSets,
  useActivityLogbook,
  useActivityPhotos,
  useDeleteActivityPhoto,
  useLogbookStatus,
  usePhotoUrl,
  useReadActivityPhoto,
  useSaveActivityLogbook,
  useUploadActivityPhoto,
} from "../api/queries";
import type { PhotoOut } from "../api/types";
import { ApiError } from "../api/client";
import { loadExerciseLibrary, type ExerciseLibraryEntry } from "../exerciseLibrary";
import {
  AUTO_MATCH_SCORE,
  draftsFromDecoded,
  draftsFromLogbook,
  draftsToPayload,
  emptyDraft,
  matchLibrary,
  moveItem,
  orderByRecorded,
  recordedBlocks,
  type ExerciseDraft,
} from "../logbookMatch";
import { Modal } from "./Modal";
import "../styles/logbook.css";

function PhotoThumb({ activityId, photo }: { activityId: string; photo: PhotoOut }) {
  const url = usePhotoUrl(activityId, photo.id);
  const [open, setOpen] = useState(false);
  if (!url.data) return <div className="logbook__thumb logbook__thumb--loading" />;
  return (
    <>
      <button
        type="button"
        className="logbook__thumb"
        onClick={() => setOpen(true)}
        aria-label="View photo"
      >
        <img src={url.data} alt="Handwritten notes" />
      </button>
      <Modal open={open} onClose={() => setOpen(false)} title="Handwritten notes">
        <img className="logbook__full" src={url.data} alt="Handwritten notes" />
      </Modal>
    </>
  );
}

function libraryLabel(entry: ExerciseLibraryEntry): string {
  return `${entry.name} (${entry.categoryLabel})`;
}

function ExerciseEditor({
  draft,
  index,
  count,
  library,
  watch,
  onChange,
  onMove,
  onRemove,
}: {
  draft: ExerciseDraft;
  index: number;
  count: number;
  library: ExerciseLibraryEntry[];
  watch: string | null;
  onChange: (next: ExerciseDraft) => void;
  onMove: (to: number) => void;
  onRemove: () => void;
}) {
  const matched = library.find(
    (e) => e.category === draft.exercise_category && e.exercise === draft.exercise_name,
  );
  const suggestions = useMemo(
    () => (matched ? [] : matchLibrary(draft.label, library, 3)),
    [matched, draft.label, library],
  );
  const choose = (entry: ExerciseLibraryEntry | null) =>
    onChange({
      ...draft,
      exercise_category: entry?.category ?? null,
      exercise_name: entry?.exercise ?? null,
    });
  const setSet = (i: number, patch: Partial<ExerciseDraft["sets"][number]>) =>
    onChange({ ...draft, sets: draft.sets.map((s, j) => (j === i ? { ...s, ...patch } : s)) });
  const num = (value: string) => (value === "" ? null : Number(value));

  return (
    <li className="logbook__exercise">
      <div className="logbook__exercise-head">
        <span className="logbook__order">{index + 1}</span>
        <input
          className="input logbook__label"
          value={draft.label}
          placeholder="Exercise as written"
          aria-label={`Exercise ${index + 1} name`}
          onChange={(e) => onChange({ ...draft, label: e.target.value })}
        />
        <div className="logbook__move">
          <button
            type="button"
            className="button"
            disabled={index === 0}
            aria-label={`Move ${draft.label || "exercise"} up`}
            onClick={() => onMove(index - 1)}
          >
            ↑
          </button>
          <button
            type="button"
            className="button"
            disabled={index === count - 1}
            aria-label={`Move ${draft.label || "exercise"} down`}
            onClick={() => onMove(index + 1)}
          >
            ↓
          </button>
          <button type="button" className="button" onClick={onRemove}>
            Remove
          </button>
        </div>
      </div>

      <div className="logbook__match">
        {matched ? (
          <>
            <span>
              Matched to <strong>{matched.name}</strong>
              {matched.primary_muscles.length > 0 && ` (${matched.primary_muscles.join(", ")})`}
            </span>
            <button type="button" className="button" onClick={() => choose(null)}>
              Change
            </button>
          </>
        ) : (
          <>
            <span className="logbook__unmatched">Not matched to an exercise yet.</span>
            {suggestions.map((m) => (
              <button
                key={`${m.entry.category}-${m.entry.exercise}`}
                type="button"
                className="button"
                onClick={() => choose(m.entry)}
              >
                {libraryLabel(m.entry)}
              </button>
            ))}
            <input
              className="input"
              list="logbook-library"
              placeholder="Search all exercises"
              aria-label={`Find a library exercise for ${draft.label || "this exercise"}`}
              onChange={(e) => {
                const found = library.find((l) => libraryLabel(l) === e.target.value);
                if (found) choose(found);
              }}
            />
          </>
        )}
      </div>

      <table className="logbook__sets">
        <thead>
          <tr>
            <th>Set</th>
            <th>Reps</th>
            <th>kg</th>
            <th>Seconds</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {draft.sets.map((s, i) => (
            <tr key={i}>
              <td>{i + 1}</td>
              <td>
                <input
                  className="input"
                  type="number"
                  inputMode="numeric"
                  min={0}
                  aria-label={`Set ${i + 1} reps`}
                  value={s.reps ?? ""}
                  onChange={(e) => setSet(i, { reps: num(e.target.value) })}
                />
              </td>
              <td>
                <input
                  className="input"
                  type="number"
                  inputMode="decimal"
                  min={0}
                  step="any"
                  aria-label={`Set ${i + 1} weight in kg`}
                  value={s.weight_kg ?? ""}
                  onChange={(e) => setSet(i, { weight_kg: num(e.target.value) })}
                />
              </td>
              <td>
                <input
                  className="input"
                  type="number"
                  inputMode="decimal"
                  min={0}
                  step="any"
                  aria-label={`Set ${i + 1} seconds`}
                  value={s.duration_s ?? ""}
                  onChange={(e) => setSet(i, { duration_s: num(e.target.value) })}
                />
              </td>
              <td>
                <button
                  type="button"
                  className="button"
                  aria-label={`Remove set ${i + 1}`}
                  onClick={() => onChange({ ...draft, sets: draft.sets.filter((_, j) => j !== i) })}
                >
                  ×
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button
        type="button"
        className="button"
        onClick={() =>
          onChange({
            ...draft,
            sets: [...draft.sets, { reps: null, weight_kg: null, duration_s: null }],
          })
        }
      >
        Add a set
      </button>
      {watch && <p className="logbook__watch">Watch, same position: {watch}</p>}
    </li>
  );
}

export function LogbookCard({ activityId }: { activityId: string }) {
  const status = useLogbookStatus();
  const photos = useActivityPhotos(activityId);
  const logbook = useActivityLogbook(activityId);
  const watchSets = useActivityExerciseSets(activityId);
  const upload = useUploadActivityPhoto(activityId);
  const remove = useDeleteActivityPhoto(activityId);
  const read = useReadActivityPhoto(activityId);
  const save = useSaveActivityLogbook(activityId);

  const [drafts, setDrafts] = useState<ExerciseDraft[]>([]);
  const [dirty, setDirty] = useState(false);
  const [library, setLibrary] = useState<ExerciseLibraryEntry[] | null>(null);
  const [editing, setEditing] = useState(false);

  // The saved logbook is the starting point, until the athlete has edited.
  useEffect(() => {
    if (logbook.data && !dirty) setDrafts(draftsFromLogbook(logbook.data.exercises));
  }, [logbook.data, dirty]);
  const showEditor = editing || drafts.length > 0;
  useEffect(() => {
    if (showEditor) void loadExerciseLibrary().then(setLibrary);
  }, [showEditor]);

  const blocks = useMemo(() => recordedBlocks(watchSets.data ?? []), [watchSets.data]);
  const edit = (next: ExerciseDraft[]) => {
    setDrafts(next);
    setDirty(true);
  };

  const applyReading = async (photo: PhotoOut) => {
    if (!photo.decoded) return;
    // The library may not be loaded yet (it is only fetched once the editor shows).
    const entries = library ?? (await loadExerciseLibrary());
    setLibrary(entries);
    const incoming = draftsFromDecoded(photo.decoded, photo.id).map((d) => {
      const best = matchLibrary(d.label, entries, 1)[0];
      return best && best.score >= AUTO_MATCH_SCORE
        ? { ...d, exercise_category: best.entry.category, exercise_name: best.entry.exercise }
        : d;
    });
    edit(incoming);
    setEditing(true);
  };

  const orderToWatch = () => edit(orderByRecorded(drafts, blocks).drafts);
  const watchAt = (i: number): string | null => {
    const block = blocks[i];
    if (!block) return null;
    const reps = block.reps.every((r) => r === block.reps[0]) ? ` of ${block.reps[0] ?? "?"}` : "";
    return `${block.sets} set${block.sets === 1 ? "" : "s"}${reps}${block.name ? `, ${block.name.toLowerCase().replace(/_/g, " ")}` : ""}`;
  };

  const message = (e: unknown) =>
    e instanceof ApiError && e.detail ? e.detail : "Something went wrong.";
  const list = photos.data ?? [];

  return (
    <section className="card logbook" id="activity-logbook">
      <h2>Handwritten notes</h2>
      <p className="chart-note">
        Attach a photo of the sets you wrote down. The sets you confirm here replace the
        watch&apos;s for the muscles worked.
      </p>

      <div className="logbook__photos">
        {list.map((photo) => (
          <div key={photo.id} className="logbook__photo">
            <PhotoThumb activityId={activityId} photo={photo} />
            <div className="logbook__photo-actions">
              {photo.decode_status === "pending" && <span role="status">Reading…</span>}
              {photo.decode_status === "failed" && (
                <span role="alert" className="logbook__error">
                  {photo.decode_error ?? "Could not read it."}
                </span>
              )}
              {photo.decode_status === "done" && photo.decoded && (
                <span>
                  {photo.decoded.legible
                    ? `${photo.decoded.exercises.length} exercises read`
                    : "No readable notes"}
                </span>
              )}
              {status.data?.can_read_handwriting && photo.decode_status !== "pending" && (
                <button
                  type="button"
                  className="button"
                  disabled={read.isPending}
                  onClick={() => read.mutate(photo.id)}
                >
                  {photo.decode_status === "done" ? "Read again" : "Read the handwriting"}
                </button>
              )}
              {photo.decode_status === "done" && photo.decoded?.exercises.length ? (
                <button type="button" className="button" onClick={() => void applyReading(photo)}>
                  Use this reading
                </button>
              ) : null}
              <button
                type="button"
                className="button"
                disabled={remove.isPending}
                onClick={() => {
                  if (window.confirm("Remove this photo? The sets you saved stay.")) {
                    remove.mutate(photo.id);
                  }
                }}
              >
                Remove photo
              </button>
            </div>
          </div>
        ))}
      </div>

      <div className="logbook__actions">
        <label className="button logbook__upload">
          {upload.isPending ? "Uploading…" : "Add a photo"}
          <input
            type="file"
            accept="image/jpeg,image/png,image/webp,image/gif"
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) upload.mutate(file);
              e.target.value = "";
            }}
          />
        </label>
        {!showEditor && (
          <button type="button" className="button" onClick={() => setEditing(true)}>
            Type the sets in
          </button>
        )}
      </div>
      {upload.isError && (
        <p role="alert" className="logbook__error">
          {message(upload.error)}
        </p>
      )}
      {read.isError && (
        <p role="alert" className="logbook__error">
          {message(read.error)}
        </p>
      )}
      {status.data && !status.data.can_read_handwriting && list.length > 0 && (
        <p className="chart-note">
          Reading handwriting needs an Anthropic API key in the server configuration
          (PERSEVERER_ANTHROPIC_API_KEY); until then, type the sets in from the photo.
        </p>
      )}

      {showEditor && (
        <div className="logbook__editor">
          <h3>Sets, in the order you did them</h3>
          {blocks.length > 0 && (
            <p className="chart-note">
              The watch recorded {blocks.length} exercise{blocks.length === 1 ? "" : "s"}. If you
              did them in a different order from your notes, move the exercises until each lines up
              with the watch&apos;s.
            </p>
          )}
          <datalist id="logbook-library">
            {(library ?? []).map((e) => (
              <option key={`${e.category}-${e.exercise}`} value={libraryLabel(e)} />
            ))}
          </datalist>
          <ol className="logbook__list">
            {drafts.map((d, i) => (
              <ExerciseEditor
                key={d.key}
                draft={d}
                index={i}
                count={drafts.length}
                library={library ?? []}
                watch={watchAt(i)}
                onChange={(next) => edit(drafts.map((x, j) => (j === i ? next : x)))}
                onMove={(to) => edit(moveItem(drafts, i, to))}
                onRemove={() => edit(drafts.filter((_, j) => j !== i))}
              />
            ))}
          </ol>
          <div className="logbook__actions">
            <button
              type="button"
              className="button"
              onClick={() => edit([...drafts, emptyDraft()])}
            >
              Add an exercise
            </button>
            {blocks.length > 0 && drafts.length > 1 && (
              <button type="button" className="button" onClick={orderToWatch}>
                Order like the watch
              </button>
            )}
            <button
              type="button"
              className="button button--primary"
              disabled={save.isPending || !dirty}
              onClick={() =>
                save.mutate(draftsToPayload(drafts), { onSuccess: () => setDirty(false) })
              }
            >
              {save.isPending ? "Saving…" : "Save the sets"}
            </button>
            {dirty && (
              <button
                type="button"
                className="button"
                onClick={() => {
                  setDirty(false);
                  setDrafts(draftsFromLogbook(logbook.data?.exercises ?? []));
                }}
              >
                Discard changes
              </button>
            )}
          </div>
          {save.isError && (
            <p role="alert" className="logbook__error">
              {message(save.error)}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
