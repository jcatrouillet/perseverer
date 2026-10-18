// The three athlete-set tags an activity list can be filtered on, shared by the Activities list
// and the run-comparison search so both offer the same choices in the same words.

/** "" leaves the tag out of the filter, "only" keeps tagged activities, "exclude" drops them. */
export type FlagChoice = "" | "only" | "exclude";

export type FlagKey = "race" | "virtual" | "commute";

export const FLAG_FILTERS: { key: FlagKey; label: string; only: string; exclude: string }[] = [
  { key: "race", label: "Race", only: "Races only", exclude: "No races" },
  { key: "virtual", label: "Virtual", only: "Virtual only", exclude: "No virtual" },
  { key: "commute", label: "Commute", only: "Commutes only", exclude: "No commutes" },
];

export type FlagChoices = Record<FlagKey, FlagChoice>;

export const NO_FLAGS: FlagChoices = { race: "", virtual: "", commute: "" };

/** The API's tri-state boolean: true = only, false = exclude, undefined = either. */
export function flagParam(choice: FlagChoice): boolean | undefined {
  return choice === "only" ? true : choice === "exclude" ? false : undefined;
}

export function parseFlagChoice(raw: string | null): FlagChoice {
  return raw === "only" || raw === "exclude" ? raw : "";
}
