// Which activities are done in shoes, so the shoe picker and the default-shoe list only appear
// where they mean something (not on a ride, a rowing session, yoga or a ski day).
const SHOE_SPORTS = new Set([
  "running",
  "walking",
  "hiking",
  "racket",
  "snowshoeing",
  "hiit",
  "training",
  "fitness_equipment",
]);

// Disciplines that arrive under a shoe sport but are done barefoot or without footwear that is
// tracked (yoga, strength and breathwork are `training` with the discipline in `sub_sport`).
const BAREFOOT_SUB_SPORTS = new Set(["yoga", "pilates", "breathing", "meditation", "stretching"]);

export function sportUsesShoes(sport: string, subSport?: string | null): boolean {
  if (!SHOE_SPORTS.has(sport)) return false;
  return !(subSport != null && BAREFOOT_SUB_SPORTS.has(subSport));
}
