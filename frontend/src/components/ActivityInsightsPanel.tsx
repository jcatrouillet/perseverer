// Point-in-time insights for one run or bouldering session (GET /activities/{id}/insights) --
// always computed from the athlete's own past, never anything that happened after this activity
// (see rules_activity.py).
// No date shown per row (we're already on this activity's own page, so its date is redundant)
// and no link (every non-streak insight here is *about* this same activity, by construction --
// see rules_activity.py's activity_id filter -- so a link would only ever point back to the
// page already open). Deliberately plain rows, not icon-chip cards: compact enough to stack
// many at once, same "bare text row" density as ActivityFastestTable rather than the boxed
// InsightCard style the athlete-wide Insights tab uses.
import type { InsightOut } from "../api/types";

export function ActivityInsightsPanel({
  insights,
  heading,
}: {
  insights: InsightOut[];
  heading: string;
}) {
  if (insights.length === 0) return null;

  return (
    <section className="card activity-insights">
      <h2>{heading}</h2>
      <ul className="activity-insights__list">
        {insights.map((insight, i) => (
          <li key={`${insight.kind}-${insight.window}-${i}`} className="activity-insights__row">
            {insight.title}
          </li>
        ))}
      </ul>
    </section>
  );
}
