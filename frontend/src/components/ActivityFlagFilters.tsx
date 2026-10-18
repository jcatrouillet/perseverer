import {
  FLAG_FILTERS,
  parseFlagChoice,
  type FlagChoices,
  type FlagKey,
} from "../activityFlagFilter";

/** One "Any / only / none" select per tag (race, virtual, commute). */
export function ActivityFlagFilters({
  values,
  onChange,
}: {
  values: FlagChoices;
  onChange: (key: FlagKey, value: FlagChoices[FlagKey]) => void;
}) {
  return (
    <>
      {FLAG_FILTERS.map((f) => (
        <label className="field" key={f.key}>
          {f.label}
          <select
            className="input"
            value={values[f.key]}
            onChange={(e) => onChange(f.key, parseFlagChoice(e.target.value))}
          >
            <option value="">Any</option>
            <option value="only">{f.only}</option>
            <option value="exclude">{f.exclude}</option>
          </select>
        </label>
      ))}
    </>
  );
}
