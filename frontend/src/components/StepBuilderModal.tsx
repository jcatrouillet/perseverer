// A GUI alternative to typing workout-syntax lines by hand (docs/adr/0015-scheduled-workouts.md
// -- the user's own explicit request: "Add a step button to help build the step with a multiple
// choice popup ... it can also have the repeat"). Deliberately *generates syntax text and
// inserts it at the cursor* rather than maintaining its own separate structured step state that
// could drift from the textarea -- the textarea/parsed preview stays the single source of truth,
// same "text is canonical" principle the rest of this feature follows.
//
// Flow: (1) time or distance duration, (2) which target(s) -- pace/HR/cadence, multi-select,
// (3) is each target a range or a single value, (4) an optional repeat count, which lets the
// athlete build a second (and further) step into the same block before generating -- the common
// "interval + recovery, x N" shape -- (5) Generate renders the line(s) (with a blank-line-
// delimited "Nx" block when repeating) and inserts them.
import { useState } from "react";

import { Modal } from "./Modal";
import "../styles/stepBuilder.css";

type DurationUnit = "m" | "s" | "km" | "mtr";
type TargetKind = "pace" | "hr" | "cadence";

interface StepDraft {
  intensity: string; // "" = none
  durationUnit: DurationUnit;
  durationValue: string;
  targets: Set<TargetKind>;
  paceRange: boolean;
  paceLow: string; // "M:SS"
  paceHigh: string;
  hrMode: "bpm" | "zone";
  hrRange: boolean;
  hrLow: string;
  hrHigh: string;
  hrZone: string;
  cadenceRange: boolean;
  cadenceLow: string;
  cadenceHigh: string;
}

function emptyStepDraft(): StepDraft {
  return {
    intensity: "",
    durationUnit: "m",
    durationValue: "",
    targets: new Set(),
    paceRange: false,
    paceLow: "",
    paceHigh: "",
    hrMode: "bpm",
    hrRange: false,
    hrLow: "",
    hrHigh: "",
    hrZone: "2",
    cadenceRange: false,
    cadenceLow: "",
    cadenceHigh: "",
  };
}

const INTENSITIES = ["", "warmup", "cooldown", "recovery", "rest", "active"];

function buildStepLine(draft: StepDraft): string | null {
  if (!draft.durationValue.trim()) return null;
  const parts: string[] = [];
  if (draft.intensity) parts.push(draft.intensity.charAt(0).toUpperCase() + draft.intensity.slice(1));
  parts.push(`${draft.durationValue.trim()}${draft.durationUnit}`);

  if (draft.targets.has("pace")) {
    if (draft.paceRange) {
      if (draft.paceLow && draft.paceHigh) parts.push(`${draft.paceLow}-${draft.paceHigh}/km Pace`);
    } else if (draft.paceLow) {
      parts.push(`${draft.paceLow}/km Pace`);
    }
  }
  if (draft.targets.has("hr")) {
    if (draft.hrMode === "zone") {
      parts.push(`Z${draft.hrZone} HR`);
    } else if (draft.hrRange) {
      if (draft.hrLow && draft.hrHigh) parts.push(`${draft.hrLow}-${draft.hrHigh} HR`);
    } else if (draft.hrLow) {
      parts.push(`${draft.hrLow} HR`);
    }
  }
  if (draft.targets.has("cadence")) {
    if (draft.cadenceRange) {
      if (draft.cadenceLow && draft.cadenceHigh) parts.push(`${draft.cadenceLow}-${draft.cadenceHigh}spm`);
    } else if (draft.cadenceLow) {
      parts.push(`${draft.cadenceLow}spm`);
    }
  }
  return parts.join(" ");
}

function StepFields({
  draft,
  onChange,
  label,
}: {
  draft: StepDraft;
  onChange: (next: StepDraft) => void;
  label: string;
}) {
  function toggleTarget(kind: TargetKind) {
    const next = new Set(draft.targets);
    if (next.has(kind)) next.delete(kind);
    else next.add(kind);
    onChange({ ...draft, targets: next });
  }

  return (
    <fieldset className="step-builder__step">
      <legend>{label}</legend>

      <label className="field">
        Intensity
        <select
          className="input"
          value={draft.intensity}
          onChange={(e) => onChange({ ...draft, intensity: e.target.value })}
        >
          {INTENSITIES.map((i) => (
            <option key={i} value={i}>
              {i === "" ? "(none)" : i.charAt(0).toUpperCase() + i.slice(1)}
            </option>
          ))}
        </select>
      </label>

      <div className="step-builder__row">
        <label className="field">
          Duration
          <input
            className="input"
            type="number"
            min="0"
            step="any"
            value={draft.durationValue}
            onChange={(e) => onChange({ ...draft, durationValue: e.target.value })}
          />
        </label>
        <label className="field">
          Time or distance?
          <select
            className="input"
            value={draft.durationUnit}
            onChange={(e) => onChange({ ...draft, durationUnit: e.target.value as DurationUnit })}
          >
            <option value="m">minutes</option>
            <option value="s">seconds</option>
            <option value="km">kilometers</option>
            <option value="mtr">meters</option>
          </select>
        </label>
      </div>

      <div className="field">
        Target
        <div className="step-builder__checkboxes">
          <label>
            <input
              type="checkbox"
              checked={draft.targets.has("pace")}
              onChange={() => toggleTarget("pace")}
            />
            Pace
          </label>
          <label>
            <input
              type="checkbox"
              checked={draft.targets.has("hr")}
              onChange={() => toggleTarget("hr")}
            />
            Heart rate
          </label>
          <label>
            <input
              type="checkbox"
              checked={draft.targets.has("cadence")}
              onChange={() => toggleTarget("cadence")}
            />
            Cadence
          </label>
        </div>
      </div>

      {draft.targets.has("pace") && (
        <fieldset className="step-builder__target">
          <legend>Pace</legend>
          <label>
            <input
              type="checkbox"
              checked={draft.paceRange}
              onChange={(e) => onChange({ ...draft, paceRange: e.target.checked })}
            />
            Range?
          </label>
          <div className="step-builder__row">
            <input
              className="input"
              placeholder="5:00"
              value={draft.paceLow}
              onChange={(e) => onChange({ ...draft, paceLow: e.target.value })}
            />
            {draft.paceRange && (
              <input
                className="input"
                placeholder="5:20"
                value={draft.paceHigh}
                onChange={(e) => onChange({ ...draft, paceHigh: e.target.value })}
              />
            )}
          </div>
        </fieldset>
      )}

      {draft.targets.has("hr") && (
        <fieldset className="step-builder__target">
          <legend>Heart rate</legend>
          <label>
            <input
              type="radio"
              name="hr-mode"
              checked={draft.hrMode === "bpm"}
              onChange={() => onChange({ ...draft, hrMode: "bpm" })}
            />
            bpm
          </label>
          <label>
            <input
              type="radio"
              name="hr-mode"
              checked={draft.hrMode === "zone"}
              onChange={() => onChange({ ...draft, hrMode: "zone" })}
            />
            Zone
          </label>
          {draft.hrMode === "zone" ? (
            <select
              className="input"
              value={draft.hrZone}
              onChange={(e) => onChange({ ...draft, hrZone: e.target.value })}
            >
              {["1", "2", "3", "4", "5"].map((z) => (
                <option key={z} value={z}>
                  Z{z}
                </option>
              ))}
            </select>
          ) : (
            <>
              <label>
                <input
                  type="checkbox"
                  checked={draft.hrRange}
                  onChange={(e) => onChange({ ...draft, hrRange: e.target.checked })}
                />
                Range?
              </label>
              <div className="step-builder__row">
                <input
                  className="input"
                  placeholder="140"
                  value={draft.hrLow}
                  onChange={(e) => onChange({ ...draft, hrLow: e.target.value })}
                />
                {draft.hrRange && (
                  <input
                    className="input"
                    placeholder="150"
                    value={draft.hrHigh}
                    onChange={(e) => onChange({ ...draft, hrHigh: e.target.value })}
                  />
                )}
              </div>
            </>
          )}
        </fieldset>
      )}

      {draft.targets.has("cadence") && (
        <fieldset className="step-builder__target">
          <legend>Cadence (spm)</legend>
          <label>
            <input
              type="checkbox"
              checked={draft.cadenceRange}
              onChange={(e) => onChange({ ...draft, cadenceRange: e.target.checked })}
            />
            Range?
          </label>
          <div className="step-builder__row">
            <input
              className="input"
              placeholder="170"
              value={draft.cadenceLow}
              onChange={(e) => onChange({ ...draft, cadenceLow: e.target.value })}
            />
            {draft.cadenceRange && (
              <input
                className="input"
                placeholder="180"
                value={draft.cadenceHigh}
                onChange={(e) => onChange({ ...draft, cadenceHigh: e.target.value })}
              />
            )}
          </div>
        </fieldset>
      )}
    </fieldset>
  );
}

export function StepBuilderModal({
  open,
  onClose,
  onGenerate,
}: {
  open: boolean;
  onClose: () => void;
  // The generated syntax text (one or more lines, blank-line-delimited if repeating) -- the
  // caller inserts it at the textarea's cursor.
  onGenerate: (text: string) => void;
}) {
  const [steps, setSteps] = useState<StepDraft[]>([emptyStepDraft()]);
  const [repeatCount, setRepeatCount] = useState("");

  function reset() {
    setSteps([emptyStepDraft()]);
    setRepeatCount("");
  }

  function handleGenerate() {
    const lines = steps.map(buildStepLine).filter((l): l is string => l != null && l.length > 0);
    if (lines.length === 0) return;
    const count = Number(repeatCount);
    const text =
      repeatCount && Number.isFinite(count) && count > 0
        ? `${count}x\n${lines.join("\n")}`
        : lines.join("\n");
    onGenerate(text);
    reset();
    onClose();
  }

  return (
    <Modal
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
      title="Add step"
    >
      <div className="step-builder">
        {steps.map((draft, i) => (
          <StepFields
            key={i}
            draft={draft}
            label={steps.length > 1 ? `Step ${i + 1}` : "Step"}
            onChange={(next) => setSteps(steps.map((s, j) => (j === i ? next : s)))}
          />
        ))}

        <label className="field">
          Repeat?
          <input
            className="input"
            type="number"
            min="2"
            step="1"
            placeholder="e.g. 4"
            value={repeatCount}
            onChange={(e) => setRepeatCount(e.target.value)}
          />
        </label>

        {repeatCount && (
          <button
            type="button"
            className="button"
            onClick={() => setSteps([...steps, emptyStepDraft()])}
          >
            + Add another step to this block
          </button>
        )}

        <button type="button" className="button button--primary" onClick={handleGenerate}>
          Generate
        </button>
      </div>
    </Modal>
  );
}
