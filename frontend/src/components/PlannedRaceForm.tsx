// The "Race" section of the Day view / Month view's expanded-day card: an upcoming race on the
// calendar with a name, distance, optional time of day, and an optional target finish time.
// Deliberately its own small component, not folded into ScheduleWorkoutForm.tsx (already 690+
// lines) -- a race has no step model, no Garmin push, none of that form's sport-tier branching.
// See db/schema.py::planned_race and planned_races.py for the target-vs-predicted-finish-time
// lookup this renders.
import { useState } from "react";

import {
  useCreatePlannedRace,
  useDeletePlannedRace,
  usePlannedRacesForDate,
  useUpdatePlannedRace,
} from "../api/queries";
import type { PlannedRaceOut } from "../api/types";
import { useDistanceFormat } from "../formatDistance";
import { useTimeFormat } from "../formatTime";
import { formatClockDuration } from "../runningStats";
import { Icon } from "./Icon";
import { LoadingSpinner } from "./LoadingSpinner";
import { TimeOfDayField } from "./TimeOfDayField";
import "../styles/plannedWorkout.css";

const SPORTS = [
  { value: "running", label: "Running" },
  { value: "trail_running", label: "Trail running" },
  { value: "cycling", label: "Cycling" },
  { value: "swimming", label: "Swimming" },
  { value: "other", label: "Other" },
];

// Preset distances match vdot.py::RACE_DISTANCES_M exactly -- these are the only distances
// planned_races.py's own predicted_duration_s_for_distance can compare a target against.
// "Custom" covers everything else (a 15K, a 50-miler) -- target-only, no prediction, honest
// rather than extrapolated.
const DISTANCE_PRESETS = [
  { value: "5000", label: "5K" },
  { value: "10000", label: "10K" },
  { value: "21097.5", label: "Half marathon" },
  { value: "42195", label: "Marathon" },
  { value: "custom", label: "Custom" },
];
const DISTANCE_MATCH_TOLERANCE_M = 1;

function distancePresetFor(distanceM: number): string {
  const match = DISTANCE_PRESETS.find(
    (p) =>
      p.value !== "custom" && Math.abs(Number(p.value) - distanceM) <= DISTANCE_MATCH_TOLERANCE_M,
  );
  return match ? match.value : "custom";
}

function formatDayCountdown(daysUntil: number): string {
  if (daysUntil < 0) return `${Math.abs(daysUntil)} day${daysUntil === -1 ? "" : "s"} ago`;
  if (daysUntil === 0) return "today";
  if (daysUntil === 1) return "tomorrow";
  return `in ${daysUntil} days`;
}

function RaceSummary({ race, onEdit }: { race: PlannedRaceOut; onEdit: () => void }) {
  const del = useDeletePlannedRace();
  const { formatDistance, metersToDisplay } = useDistanceFormat();
  const { formatHHMM } = useTimeFormat();
  const sportLabel = SPORTS.find((s) => s.value === race.sport)?.label ?? race.sport;

  let comparison: string | null = null;
  let comparisonClass = "";
  if (race.predicted_duration_s != null && race.target_duration_s != null) {
    const diff = race.target_duration_s - race.predicted_duration_s;
    comparison =
      diff >= 0
        ? `Predicted ${formatClockDuration(race.predicted_duration_s)} — on track`
        : `Predicted ${formatClockDuration(race.predicted_duration_s)} — ` +
          `${formatClockDuration(-diff)} over target`;
    comparisonClass =
      diff >= 0 ? "planned-race__comparison--good" : "planned-race__comparison--warn";
  } else if (race.predicted_duration_s != null) {
    comparison = `Predicted ${formatClockDuration(race.predicted_duration_s)}`;
  }

  return (
    <div className="planned-race__summary">
      <div className="planned-race__header">
        <Icon name="trophy" />
        <strong>{race.name}</strong>
        <span className="chart-note">({sportLabel})</span>
      </div>
      <p className="chart-note">
        {race.scheduled_time && `${formatHHMM(race.scheduled_time)} · `}
        {formatDistance(
          race.distance_m,
          Number.isInteger(metersToDisplay(race.distance_m)) ? 0 : 1,
        )}
        {race.target_duration_s != null &&
          ` · Target sub ${formatClockDuration(race.target_duration_s)}`}
        {" · "}
        {formatDayCountdown(race.days_until)}
      </p>
      {comparison && <p className={`chart-note ${comparisonClass}`}>{comparison}</p>}
      <div className="planned-workout__actions">
        <button type="button" className="button" onClick={onEdit}>
          Edit
        </button>
        <button
          type="button"
          className="button"
          onClick={() => del.mutate(race.id)}
          disabled={del.isPending}
        >
          Delete
        </button>
      </div>
    </div>
  );
}

function secondsToHms(totalSeconds: number | null): { h: string; m: string; s: string } {
  if (totalSeconds == null) return { h: "", m: "", s: "" };
  const total = Math.round(totalSeconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return { h: h ? String(h) : "", m: String(m), s: String(s) };
}

function hmsToSeconds(h: string, m: string, s: string): number | null {
  const hNum = Number(h) || 0;
  const mNum = Number(m) || 0;
  const sNum = Number(s) || 0;
  const total = hNum * 3600 + mNum * 60 + sNum;
  return total > 0 ? total : null;
}

function RaceEditForm({
  localDate,
  raceId,
  initial,
  onDone,
}: {
  localDate: string;
  raceId: number | null; // null means "creating a new race on this date"
  initial?: PlannedRaceOut;
  onDone: () => void;
}) {
  const create = useCreatePlannedRace();
  const update = useUpdatePlannedRace();
  const { unitLabel, metersToDisplay, displayToMeters } = useDistanceFormat();

  const [name, setName] = useState(initial?.name ?? "");
  const [sport, setSport] = useState(initial?.sport ?? "running");
  const [distancePreset, setDistancePreset] = useState(
    initial ? distancePresetFor(initial.distance_m) : "10000",
  );
  const [customDistance, setCustomDistance] = useState(
    initial && distancePresetFor(initial.distance_m) === "custom"
      ? String(metersToDisplay(initial.distance_m))
      : "",
  );
  const [scheduledTime, setScheduledTime] = useState(initial?.scheduled_time ?? "");
  const initialTarget = secondsToHms(initial?.target_duration_s ?? null);
  const [targetH, setTargetH] = useState(initialTarget.h);
  const [targetM, setTargetM] = useState(initialTarget.m);
  const [targetS, setTargetS] = useState(initialTarget.s);

  const saving = raceId == null ? create.isPending : update.isPending;
  const error = raceId == null ? create.isError : update.isError;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const distanceM =
      distancePreset === "custom"
        ? displayToMeters(Number(customDistance))
        : Number(distancePreset);
    if (!name.trim() || !(distanceM > 0)) return;
    const fields = {
      local_date: localDate,
      name: name.trim(),
      sport,
      distance_m: distanceM,
      scheduled_time: scheduledTime || null,
      target_duration_s: hmsToSeconds(targetH, targetM, targetS),
    };
    if (raceId == null) {
      create.mutate(fields, { onSuccess: onDone });
    } else {
      update.mutate({ raceId, ...fields }, { onSuccess: onDone });
    }
  }

  return (
    <form className="planned-workout-form" onSubmit={handleSubmit}>
      <div className="planned-workout-form__row">
        <label className="field">
          Name
          <input
            className="input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Paris Marathon"
            required
          />
        </label>
        <label className="field">
          Sport
          <select className="input" value={sport} onChange={(e) => setSport(e.target.value)}>
            {SPORTS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="planned-workout-form__row">
        <label className="field">
          Distance
          <select
            className="input"
            value={distancePreset}
            onChange={(e) => setDistancePreset(e.target.value)}
          >
            {DISTANCE_PRESETS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        {distancePreset === "custom" && (
          <label className="field">
            Distance ({unitLabel})
            <input
              className="input"
              type="number"
              inputMode="decimal"
              min="0"
              step="0.1"
              value={customDistance}
              onChange={(e) => setCustomDistance(e.target.value)}
              placeholder="e.g. 15"
              required
            />
          </label>
        )}
        <label className="field">
          Time of day (optional)
          <TimeOfDayField value={scheduledTime} onChange={setScheduledTime} />
        </label>
      </div>

      <label className="field">
        Target finish time (optional)
        <div className="planned-race__target-inputs">
          <input
            className="input"
            type="number"
            inputMode="numeric"
            min="0"
            placeholder="hh"
            value={targetH}
            onChange={(e) => setTargetH(e.target.value)}
          />
          <span>:</span>
          <input
            className="input"
            type="number"
            inputMode="numeric"
            min="0"
            max="59"
            placeholder="mm"
            value={targetM}
            onChange={(e) => setTargetM(e.target.value)}
          />
          <span>:</span>
          <input
            className="input"
            type="number"
            inputMode="numeric"
            min="0"
            max="59"
            placeholder="ss"
            value={targetS}
            onChange={(e) => setTargetS(e.target.value)}
          />
        </div>
      </label>

      <div className="planned-workout-form__actions">
        <button type="submit" className="button button--primary" disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
        <button type="button" className="button" onClick={onDone}>
          Cancel
        </button>
        {error && (
          <span role="alert" className="settings-form__error">
            Could not save.
          </span>
        )}
      </div>
    </form>
  );
}

export function PlannedRaceForm({ localDate }: { localDate: string }) {
  const races = usePlannedRacesForDate(localDate);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [addingNew, setAddingNew] = useState(false);

  if (races.isLoading) return <LoadingSpinner size="sm" />;
  if (races.isError) return <p role="alert">Could not load scheduled races.</p>;

  const list = races.data ?? [];

  function stopEditing() {
    setEditingId(null);
    setAddingNew(false);
  }

  if (addingNew) {
    return <RaceEditForm localDate={localDate} raceId={null} onDone={stopEditing} />;
  }
  if (editingId != null) {
    const editing = list.find((r) => r.id === editingId);
    return (
      <RaceEditForm
        localDate={localDate}
        raceId={editingId}
        initial={editing}
        onDone={stopEditing}
      />
    );
  }

  return (
    <>
      {list.map((race) => (
        <div key={race.id} className="planned-workout__entry">
          <RaceSummary race={race} onEdit={() => setEditingId(race.id)} />
        </div>
      ))}
      <div className="planned-workout__actions">
        <button type="button" className="button button--primary" onClick={() => setAddingNew(true)}>
          {list.length === 0 ? "Add a race" : "Add another race"}
        </button>
      </div>
    </>
  );
}
