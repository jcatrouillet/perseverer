// "Add a graph with all the routes climbed" -- one stacked bar per grade, red "Attempted"
// segment under a green "Completed" segment, matching the reference image the athlete provided.
// Reusable for both the single-activity detail page (gradeBreakdownFromRoutes, computed
// client-side from that one activity's own splits) and the period-summary page (the real
// GET /activities/climbing-summary aggregate, spanning however many sessions fall in the
// period) -- see boulderingRoutes.ts's own docstring for why those two use different data
// sources for the same shape.
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { ClimbGradeBreakdownOut } from "../api/types";
import { formatGrade } from "../boulderingRoutes";
import { ChartLegend } from "./ChartLegend";

export function ClimbGradeChart({ gradeBreakdown }: { gradeBreakdown: ClimbGradeBreakdownOut[] }) {
  if (gradeBreakdown.length === 0) return null;

  const data = gradeBreakdown.map((b) => ({
    grade: formatGrade(b.grade),
    Attempted: b.attempted,
    Completed: b.completed,
  }));

  return (
    <div className="climb-grade-chart">
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={data}>
          <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="grade"
            stroke="var(--color-text-muted)"
            fontSize={11}
            label={{ value: "Route Difficulty", position: "insideBottom", offset: -5, fontSize: 11 }}
          />
          <YAxis
            stroke="var(--color-text-muted)"
            fontSize={11}
            width={32}
            allowDecimals={false}
            label={{ value: "Routes Climbed", angle: -90, position: "insideLeft", fontSize: 11 }}
          />
          <Tooltip
            contentStyle={{
              background: "var(--color-surface-raised)",
              border: "1px solid var(--color-border)",
            }}
          />
          <Bar dataKey="Attempted" stackId="grade" fill="var(--color-danger)" isAnimationActive={false} />
          <Bar dataKey="Completed" stackId="grade" fill="var(--color-success)" isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
      <ChartLegend
        center
        items={[
          { label: "Attempted", color: "var(--color-danger)" },
          { label: "Completed", color: "var(--color-success)" },
        ]}
      />
    </div>
  );
}
