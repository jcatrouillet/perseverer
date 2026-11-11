// Activity page, HIIT / strength only: the muscles the recorded sets worked, drawn on a body and
// listed with their weighted set counts. Shown only when Garmin recorded sets for the activity;
// an activity without sets (the watch logged one summary set, or none) shows no card at all.
import { useEffect, useState } from "react";

import { useActivityExerciseSets, useActivityLogbook } from "../api/queries";
import { loadExerciseLibrary, type ExerciseLibraryEntry } from "../exerciseLibrary";
import { logbookAsSets } from "../logbookMatch";
import { musclesWorked } from "../musclesWorked";
import { BodyMuscleMap } from "./BodyMuscleMap";
import "../styles/body-muscles.css";

export function MusclesWorkedCard({ activityId }: { activityId: string }) {
  const watch = useActivityExerciseSets(activityId);
  const logbook = useActivityLogbook(activityId);
  // The athlete's own confirmed sets, when there are any, are the truth; else the watch's.
  const fromNotes = (logbook.data?.exercises.length ?? 0) > 0;
  const sets = {
    data: fromNotes ? logbookAsSets(logbook.data?.exercises ?? []) : watch.data,
  };
  const [library, setLibrary] = useState<ExerciseLibraryEntry[] | null>(null);
  const hasSets = (sets.data?.length ?? 0) > 0;

  // The library is ~1 MB: only fetched once this activity actually has sets to place.
  useEffect(() => {
    if (!hasSets) return;
    let cancelled = false;
    void loadExerciseLibrary().then((entries) => {
      if (!cancelled) setLibrary(entries);
    });
    return () => {
      cancelled = true;
    };
  }, [hasSets]);

  if (!hasSets || !sets.data || !library) return null;
  const worked = musclesWorked(sets.data, library);
  if (worked.muscles.length === 0) return null;

  return (
    <section className="card muscles-worked" id="activity-muscles">
      <h2>Muscles worked</h2>
      <div className="muscles-worked__layout">
        <BodyMuscleMap muscles={worked.muscles} />
        <div>
          <table className="muscles-worked__table">
            <thead>
              <tr>
                <th>Muscle</th>
                <th aria-label="Intensity" />
                <th>Sets</th>
              </tr>
            </thead>
            <tbody>
              {worked.muscles.map((m) => (
                <tr key={m.muscle}>
                  <td>{m.muscle}</td>
                  <td>
                    <span
                      className="muscles-worked__bar"
                      style={{ "--intensity": m.intensity } as React.CSSProperties}
                    />
                  </td>
                  <td>{Number.isInteger(m.sets) ? m.sets : m.sets.toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="chart-note">
            From {worked.workingSets - worked.unmatchedSets} of {worked.workingSets} sets
            {fromNotes ? " in your notes" : ""}. A set counts 1 for each muscle the exercise works
            most and 0.5 for each it works less; colour is relative to the hardest-worked muscle of
            this activity.
            {worked.unmatchedSets > 0 &&
              ` ${worked.unmatchedSets} set${worked.unmatchedSets === 1 ? "" : "s"} could not be matched to an exercise.`}
          </p>
        </div>
      </div>
    </section>
  );
}
