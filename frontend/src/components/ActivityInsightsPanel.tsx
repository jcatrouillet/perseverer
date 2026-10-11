// Point-in-time insights for one run or bouldering session (GET /activities/{id}/insights) --
// always computed from the athlete's own past, never anything that happened after this activity
// (see rules_activity.py).
// No date shown per row (we're already on this activity's own page, so its date is redundant)
// and no link (every non-streak insight here is *about* this same activity, by construction --
// see rules_activity.py's activity_id filter -- so a link would only ever point back to the
// page already open). Deliberately plain rows, not icon-chip cards: compact enough to stack
// many at once, same "bare text row" density as ActivityFastestTable rather than the boxed
// InsightCard style the athlete-wide Insights tab uses. The one exception is a run's "pacing"
// row (negative/even/positive split), shown in the athlete's own unit with its paces beneath.
import type { InsightOut } from "../api/types";
import { useDistanceFormat } from "../formatDistance";
import { pacingText } from "../runPacing";

export function ActivityInsightsPanel({
  insights,
  heading,
}: {
  insights: InsightOut[];
  heading: string;
}) {
  const { unit } = useDistanceFormat();
  if (insights.length === 0) return null;

  return (
    <section className="card activity-insights">
      <h2>{heading}</h2>
      <ul className="activity-insights__list">
        {insights.map((insight, i) => {
          const key = `${insight.kind}-${insight.window}-${i}`;
          if (insight.kind === "pacing") {
            const { title, paces } = pacingText(insight.detail, insight.title, unit);
            return (
              <li key={key} className="activity-insights__row activity-insights__row--pacing">
                {title}
                {paces && <span className="activity-insights__paces">{paces}</span>}
              </li>
            );
          }
          return (
            <li key={key} className="activity-insights__row">
              {insight.title}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
