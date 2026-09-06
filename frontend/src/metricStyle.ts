// The colour chart, as data. One place decides which hue and which glyph every sport and every
// health metric gets, so a tile, a chip and a chart series can never disagree about what colour
// "running" is.
//
// The governing rule (see docs/adr/0010-phase-6.1-frontend-design.md): a hue on screen
// identifies a metric or a sport family. There is no decorative colour. That's what keeps the
// palette from sprawling as pages get added.
//
// Tones map onto the `--color-*` tokens theme.css already ships -- no new colour enters the
// codebase here, and both themes keep working with no extra work.
import type { IconName } from "./components/Icon";

export type Tone = "pace" | "hr" | "elevation" | "power" | "cadence" | "load" | "neutral";

export interface MetricStyle {
  tone: Tone;
  icon: IconName;
}

/** The same seven tones the `.tone-*` classes bind in layout.css, as values a chart library can
 * be handed directly. Charts previously picked colours by array index, which meant a metric
 * could be red in a chart and teal on the tile right above it -- routing both through here is
 * what actually enforces "a hue identifies a metric". */
const TONE_COLORS: Record<Tone, string> = {
  pace: "var(--color-pace)",
  hr: "var(--color-heart-rate)",
  elevation: "var(--color-elevation)",
  power: "var(--color-power)",
  cadence: "var(--color-cadence)",
  load: "var(--color-load)",
  neutral: "var(--color-text-muted)",
};

export function toneColor(tone: Tone): string {
  return TONE_COLORS[tone];
}

/** Ten-plus sports, five hues. Giving every sport its own colour is exactly the "too colourful"
 * failure mode -- grouping them by what the activity *is* means the year-view type list reads as
 * four or five signals instead of fourteen. Families:
 *
 *   pace      road/pace     running, cycling, rowing
 *   elevation trail/terrain walking, hiking, snowshoeing, alpine skiing
 *   power     strength      strength training, hiit, fitness equipment
 *   cadence   mobility      yoga, breathing
 *   load      skill/play    rock climbing, racket
 *
 * Keys are the raw `sport`/`sub_sport` values as they arrive from the API (snake_case), not
 * display strings. The list was built from the real catalog -- all 14 sports present across the
 * full archive -- rather than from a guess at Garmin's enum. */
const SPORT_STYLES: Record<string, MetricStyle> = {
  running: { tone: "pace", icon: "run" },
  cycling: { tone: "pace", icon: "bike" },
  rowing: { tone: "pace", icon: "waves" },

  walking: { tone: "elevation", icon: "walk" },
  hiking: { tone: "elevation", icon: "hike" },
  snowshoeing: { tone: "elevation", icon: "snow" },
  alpine_skiing: { tone: "elevation", icon: "snow" },

  strength_training: { tone: "power", icon: "dumbbell" },
  fitness_equipment: { tone: "power", icon: "dumbbell" },
  hiit: { tone: "power", icon: "bolt" },

  yoga: { tone: "cadence", icon: "yoga" },
  breathing: { tone: "cadence", icon: "yoga" },

  rock_climbing: { tone: "load", icon: "climb" },
  racket: { tone: "load", icon: "racket" },
};

/** An unmapped sport gets a neutral tone and a generic glyph rather than an arbitrary colour --
 * the same posture as the schema's "a new activity type needs a registry row, not a code
 * change". Nothing breaks the first time Garmin reports a sport nobody here has done yet; it
 * just renders quietly until someone decides which family it belongs to. */
export function sportStyle(sport: string): MetricStyle {
  return SPORT_STYLES[sport] ?? { tone: "neutral", icon: "calendar" };
}

/** The known sport catalog, for a picker (e.g. the manual sport-correction control) that should
 * only offer sports this app actually has an icon/tone for -- picking one outside this list
 * would still work (sport is a free-form string everywhere else), it just wouldn't render with
 * a real icon anywhere until someone adds it here. */
export const KNOWN_SPORTS: readonly string[] = Object.keys(SPORT_STYLES);

/** Keyed by the `logical_metric` values GET /health/dashboard returns (see
 * api/routers/health.py::LOGICAL_METRICS), not by raw vendor metric_key. */
const HEALTH_METRIC_STYLES: Record<string, MetricStyle> = {
  steps: { tone: "pace", icon: "steps" },
  calories: { tone: "load", icon: "flame" },
  resting_heart_rate: { tone: "hr", icon: "heart" },
  max_heart_rate: { tone: "hr", icon: "heart" },
  floors_ascended: { tone: "elevation", icon: "stairs" },
  vo2max: { tone: "cadence", icon: "gauge" },
  hrv_nightly_average: { tone: "cadence", icon: "pulse" },
  // Green rather than a second teal: HRV, SpO2 and stress share one chart, so their tones have
  // to stay distinguishable from each other. Green also carries the "saturated / healthy"
  // reading that suits an oxygen measure.
  spo2_average: { tone: "elevation", icon: "waves" },
  stress_average: { tone: "load", icon: "bolt" },
  // Distinct tones, not just distinct icons -- these two appear together on one chart (the week
  // view's wellness section), so they need the same kind of separation HRV/SpO2/stress already
  // have from each other, per this file's own governing rule.
  sleep_respiration_rate: { tone: "cadence", icon: "pulse" },
  waking_respiration_rate: { tone: "power", icon: "pulse" },
  // Body composition (Eufy smart scale -- see adapters/eufy.py). No dedicated scale/body glyph
  // exists in the sprite, so these reuse the closest existing icon rather than adding one just
  // for this section. Tones are chosen so every metric that can appear *together* on one of
  // HealthPage's grouped body-composition trend charts (BODY_COMPOSITION_MASS/PERCENT/INDEX_
  // METRICS) gets a distinct tone from its chart-mates -- same rule as HRV/SpO2/Stress and the
  // two respiration metrics above.
  weight_kg: { tone: "power", icon: "gauge" },
  muscle_mass_kg: { tone: "pace", icon: "dumbbell" },
  body_fat_pct: { tone: "load", icon: "flame" },
  water_pct: { tone: "elevation", icon: "droplet" },
  protein_ratio_pct: { tone: "power", icon: "dumbbell" },
  bmi: { tone: "cadence", icon: "trend" },
  bone_mass_kg: { tone: "elevation", icon: "mountain" },
  visceral_fat: { tone: "load", icon: "gauge" },
  metabolic_age: { tone: "power", icon: "clock" },
  bmr_kcal: { tone: "load", icon: "flame" },
  // Fitness & Form tab (Lactate threshold chart) -- pace and heart rate share one chart on two
  // axes, so they need distinct tones from each other, same rule as every other paired chart.
  lactate_threshold_speed: { tone: "pace", icon: "gauge" },
  lactate_threshold_heart_rate: { tone: "hr", icon: "heart" },
  // Health tab -- blood pressure's two readings share one chart (systolic/diastolic), and sleep
  // gets its own single-line chart (not a real logical_metric -- see GET /sleep, a dedicated
  // table -- so "sleep_hours" is a synthetic key this app's own frontend invents, only ever used
  // as a healthMetricStyle() lookup key, never sent to or read from the API).
  blood_pressure_systolic: { tone: "hr", icon: "heart" },
  blood_pressure_diastolic: { tone: "load", icon: "heart" },
  sleep_hours: { tone: "cadence", icon: "moon" },
};

export function healthMetricStyle(logicalMetric: string): MetricStyle {
  return HEALTH_METRIC_STYLES[logicalMetric] ?? { tone: "neutral", icon: "pulse" };
}
