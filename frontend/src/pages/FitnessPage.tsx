import { useState } from "react";

import { useFitness, useHealthObservations } from "../api/queries";
import { FitnessChart } from "../components/FitnessChart";
import { isoDate } from "../dateUtils";

const READINESS_SCORE_KEY = "garmin.export.TrainingReadinessDTO.score";
const TRAINING_STATUS_KEY = "garmin.export.TrainingHistory.trainingStatus";

function defaultRange(): { start: string; end: string } {
  const end = new Date();
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - 90); // ~3 months -- enough to see CTL/ATL trend
  return { start: isoDate(start), end: isoDate(end) };
}

export function FitnessPage() {
  const [range, setRange] = useState(defaultRange);
  const fitness = useFitness(range.start, range.end);
  const garmin = useHealthObservations(
    [READINESS_SCORE_KEY, TRAINING_STATUS_KEY],
    range.start,
    range.end,
  );

  const readinessScores =
    garmin.data?.items.filter((o) => o.metric_key === READINESS_SCORE_KEY) ?? [];
  const trainingStatuses =
    garmin.data?.items.filter((o) => o.metric_key === TRAINING_STATUS_KEY) ?? [];
  const latestReadiness = readinessScores[readinessScores.length - 1];

  return (
    <main>
      <h1>Fitness &amp; Form</h1>
      <form>
        <label>
          From
          <input
            type="date"
            value={range.start}
            onChange={(e) => setRange((r) => ({ ...r, start: e.target.value }))}
          />
        </label>
        <label>
          To
          <input
            type="date"
            value={range.end}
            onChange={(e) => setRange((r) => ({ ...r, end: e.target.value }))}
          />
        </label>
      </form>

      {fitness.isLoading && <p>Loading…</p>}
      {fitness.isError && <p role="alert">Could not load Fitness &amp; Form.</p>}
      {fitness.data && <FitnessChart series={fitness.data} />}

      <section>
        <h2>Compared against Garmin&rsquo;s own signals</h2>
        <p>
          Garmin&rsquo;s own exports have no independent CTL/ATL/TSB — the chart above is computed
          independently from your training load. These are Garmin&rsquo;s own precomputed Training
          Readiness and Training Status, shown alongside for comparison, not expected to match
          exactly.
        </p>
        {latestReadiness && (
          <p>
            Latest Garmin Training Readiness score ({latestReadiness.local_date}):{" "}
            {latestReadiness.value_num}
          </p>
        )}
        {trainingStatuses.length > 0 && (
          <ul>
            {trainingStatuses.slice(-14).map((o) => (
              <li key={o.observed_at_utc}>
                {o.local_date}: {o.value_text}
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
