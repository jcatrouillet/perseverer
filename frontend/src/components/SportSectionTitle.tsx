// The heading of a sport's section in the period summaries (Running, Cycling, Hikes, Climbing):
// the sport's own icon chip and tone from metricStyle.ts, then the title, on one line. One
// component so every sport section looks the same.
import { sportStyle } from "../metricStyle";
import { Icon } from "./Icon";

export function SportSectionTitle({ sport, children }: { sport: string; children: string }) {
  const style = sportStyle(sport);
  return (
    <h2 className="section-title">
      <span className={`tone-${style.tone} icon-chip`}>
        <Icon name={style.icon} />
      </span>
      {children}
    </h2>
  );
}
