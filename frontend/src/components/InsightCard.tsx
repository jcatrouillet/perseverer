// One insight (rules engine, docs/ARCHITECTURE.md),
// presented on the same icon-chip/tone system every other card in this app already uses. Links
// to its subject activity when one exists; otherwise renders as a plain (non-clickable) card.
import { Link } from "wouter";

import type { InsightOut } from "../api/types";
import type { Tone } from "../metricStyle";
import { Icon, type IconName } from "./Icon";
import "../styles/insights.css";

const KIND_STYLE: Record<string, { icon: IconName; tone: Tone }> = {
  effort: { icon: "bolt", tone: "pace" },
  streak: { icon: "flame", tone: "load" },
  pb: { icon: "trophy", tone: "pace" },
  // Deliberately not "trophy" -- a window_best is a weaker claim than an all-time PB (see
  // rules_pb.py's own docstring), and reusing the trophy icon would visually overstate it.
  window_best: { icon: "trend", tone: "pace" },
  load: { icon: "gauge", tone: "load" },
  health: { icon: "heart", tone: "hr" },
};

export function InsightCard({ insight }: { insight: InsightOut }) {
  const style = KIND_STYLE[insight.kind] ?? { icon: "bolt", tone: "neutral" as Tone };

  const body = (
    <div className={`insight-card tone-${style.tone}`}>
      <span className="icon-chip">
        <Icon name={style.icon} />
      </span>
      <div className="insight-card__body">
        <span className="insight-card__title">{insight.title}</span>
        {insight.local_date && <span className="insight-card__date">{insight.local_date}</span>}
      </div>
    </div>
  );

  if (insight.activity_id) {
    return (
      <Link href={`/activities/${insight.activity_id}`} className="insight-card__link">
        {body}
      </Link>
    );
  }
  return body;
}
