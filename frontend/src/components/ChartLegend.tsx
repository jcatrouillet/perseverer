// Plain HTML legend instead of Recharts' built-in <Legend/>: keeps styling consistent with the
// rest of the design system (colors/spacing from theme.css), and avoids Recharts needing to
// measure the legend's own rendered size to lay out the plot area above it.
export function ChartLegend({
  items,
  center,
}: {
  items: { label: string; color: string }[];
  center?: boolean;
}) {
  return (
    <div className={`chart-legend${center ? " chart-legend--center" : ""}`}>
      {items.map((item) => (
        <span key={item.label} className="chart-legend__item">
          <span className="chart-legend__swatch" style={{ background: item.color }} />
          {item.label}
        </span>
      ))}
    </div>
  );
}
