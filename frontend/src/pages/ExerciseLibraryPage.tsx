// Browsable reference for every exercise the hiit/strength_training picker supports
// (docs/adr/0015-scheduled-workouts.md's own addendum) -- categories collapsed by default
// (native <details>, same convention as ActivitySourcesPanel.tsx's "Why these were merged"),
// each expanding to its own exercise list. A search narrows the list and auto-expands whichever
// categories still have a match, same UX as ExerciseStepEditor's own picker. Clicking a thumbnail
// opens it full-size in Modal.tsx's own lightbox variant -- these thumbnails are only 84px, too
// small to actually see the exercise being demonstrated.
import { useEffect, useMemo, useState } from "react";

import { LoadingSpinner } from "../components/LoadingSpinner";
import { Modal } from "../components/Modal";
import { loadExerciseLibrary, type ExerciseLibraryEntry } from "../exerciseLibrary";
import "../styles/exerciseLibrary.css";

function ExerciseCard({
  entry,
  onEnlarge,
}: {
  entry: ExerciseLibraryEntry;
  onEnlarge: (entry: ExerciseLibraryEntry) => void;
}) {
  const allMuscles = [...entry.primary_muscles, ...entry.secondary_muscles];
  return (
    <li className="exercise-library__item">
      {entry.image_url ? (
        <button
          type="button"
          className="exercise-library__thumb"
          onClick={() => onEnlarge(entry)}
          aria-label={`Enlarge photo of ${entry.name}`}
        >
          <img src={entry.image_url} alt="" loading="lazy" />
        </button>
      ) : (
        <div className="exercise-library__thumb exercise-library__thumb--empty">
          <span>No photo</span>
        </div>
      )}
      <div className="exercise-library__info">
        <div className="exercise-library__heading">
          <p className="exercise-library__name">{entry.name}</p>
          {entry.difficulty && (
            <span className="exercise-library__difficulty">{entry.difficulty}</span>
          )}
        </div>
        {allMuscles.length > 0 && (
          <p className="exercise-library__muscles">{allMuscles.join(", ")}</p>
        )}
        {entry.description && <p className="exercise-library__description">{entry.description}</p>}
        {entry.tier === 2 && entry.matched_exercise_name !== entry.name && (
          <p className="exercise-library__note">
            Photo and instructions from a closely related exercise: {entry.matched_exercise_name}
          </p>
        )}
        {entry.garmin_url && (
          <a
            className="exercise-library__link"
            href={entry.garmin_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            View on Garmin Connect
          </a>
        )}
      </div>
    </li>
  );
}

export function ExerciseLibraryPage() {
  const [entries, setEntries] = useState<ExerciseLibraryEntry[] | null>(null);
  const [query, setQuery] = useState("");
  const [lightbox, setLightbox] = useState<ExerciseLibraryEntry | null>(null);

  useEffect(() => {
    let cancelled = false;
    void loadExerciseLibrary().then((data) => {
      if (!cancelled) setEntries(data);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const isFiltering = query.trim().length > 0;

  const grouped = useMemo(() => {
    if (!entries) return [];
    const q = query.trim().toLowerCase();
    const byCategory = new Map<string, ExerciseLibraryEntry[]>();
    for (const e of entries) {
      if (q && !e.name.toLowerCase().includes(q)) continue;
      const list = byCategory.get(e.categoryLabel) ?? [];
      list.push(e);
      byCategory.set(e.categoryLabel, list);
    }
    return Array.from(byCategory.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [entries, query]);

  if (!entries) {
    return (
      <main className="exercise-library">
        <h1>Exercises</h1>
        <LoadingSpinner />
      </main>
    );
  }

  const totalMatches = grouped.reduce((sum, [, list]) => sum + list.length, 0);
  const categoryCount = new Set(entries.map((e) => e.category)).size;

  return (
    <main className="exercise-library">
      <h1>Exercises</h1>
      <p className="chart-note">
        Every exercise the HIIT and strength training scheduler supports &mdash; {entries.length}{" "}
        across {categoryCount} categories.
      </p>
      <input
        className="input exercise-library__search"
        type="search"
        placeholder="Search an exercise&hellip;"
        aria-label="Search exercises"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      {isFiltering && (
        <p className="chart-note">
          {totalMatches} match{totalMatches === 1 ? "" : "es"}
        </p>
      )}
      {isFiltering && totalMatches === 0 && (
        <p className="chart-note">No exercise matches &ldquo;{query.trim()}&rdquo;.</p>
      )}
      {grouped.map(([categoryLabel, list]) => (
        <details key={categoryLabel} className="exercise-library__category" open={isFiltering}>
          <summary>
            {categoryLabel} <span className="exercise-library__count">{list.length}</span>
          </summary>
          <ul className="exercise-library__list">
            {list.map((e) => (
              <ExerciseCard
                key={`${e.category}-${e.exercise}`}
                entry={e}
                onEnlarge={setLightbox}
              />
            ))}
          </ul>
        </details>
      ))}

      <Modal
        open={lightbox != null}
        onClose={() => setLightbox(null)}
        title={lightbox?.name ?? ""}
        panelClassName="modal__panel--image"
      >
        {lightbox?.image_url && <img src={lightbox.image_url} alt={lightbox.name} />}
      </Modal>
    </main>
  );
}
