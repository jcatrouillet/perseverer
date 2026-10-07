// The one stat presentation used everywhere (year/month/all-time stats, running stats, health).
// Replaces the ad-hoc `.stat-tile` markup that was duplicated across five files.
//
// Shape follows the "split on a race clock" idea from the design system: a small tracked-out
// caption, a large tabular numeral, and the unit demoted inside the value so the figure itself
// carries the line. Colour arrives only through `tone` (an icon chip in that metric's hue).
//
// `hero` promotes a tile to a soft wash of its own hue plus a coloured numeral. Cap it at three
// per card -- that restraint is the whole reason three tinted tiles read as emphasis rather
// than as decoration.
import type { Tone } from "../metricStyle";
import { Icon, type IconName } from "./Icon";

export function StatTile({
  label,
  value,
  unit,
  meta,
  icon,
  tone = "neutral",
  hero = false,
}: {
  label: string;
  value: string | number;
  unit?: string;
  meta?: string | null;
  icon?: IconName;
  tone?: Tone;
  hero?: boolean;
}) {
  return (
    <div className={`stat-tile tone-${tone}${hero ? " stat-tile--hero" : ""}`}>
      <span className="stat-tile__label">
        {icon && (
          <span className="icon-chip">
            <Icon name={icon} />
          </span>
        )}
        {label}
      </span>
      <span className="stat-tile__value">
        {value}
        {unit && <small>{unit}</small>}
      </span>
      {meta && <span className="stat-tile__meta">{meta}</span>}
    </div>
  );
}

/** The sport/metric pill used in "Activities by type" and anywhere a category needs naming in
 * its own hue. Same tone vocabulary as StatTile, so the two never drift apart. */
export function MetricChip({
  label,
  icon,
  tone = "neutral",
}: {
  label: string;
  icon?: IconName;
  tone?: Tone;
}) {
  return (
    <span className={`chip tone-${tone}`}>
      {icon && <Icon name={icon} />}
      {label}
    </span>
  );
}
