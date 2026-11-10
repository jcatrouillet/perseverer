// A front and a back view of the body with each muscle coloured by how hard it was worked: a
// neutral grey when it was not, from pale to deep orange as its share of the hardest-worked
// muscle grows. The shapes (data/bodyMuscleShapes.ts) are keyed by the exercise library's muscle
// names, see musclesWorked.ts.
import { BACK_SHAPES, FRONT_SHAPES, type BodyShape } from "../data/bodyMuscleShapes";
import type { MuscleLoad } from "../musclesWorked";
import "../styles/body-muscles.css";

function muscleFill(load: MuscleLoad | undefined): string | undefined {
  if (!load || load.intensity <= 0) return undefined;
  // From pale peach at the lightest worked muscle to deep orange at the hardest.
  const hot = Math.round(25 + 75 * load.intensity);
  return `color-mix(in srgb, var(--muscle-hot) ${hot}%, var(--muscle-pale))`;
}

function Figure({
  label,
  shapes,
  loads,
}: {
  label: string;
  shapes: BodyShape[];
  loads: Map<string, MuscleLoad>;
}) {
  return (
    <g aria-label={label}>
      {shapes.map((shape, i) => {
        const load = shape.muscle ? loads.get(shape.muscle) : undefined;
        const fill = muscleFill(load);
        const title = shape.muscle
          ? load
            ? `${shape.muscle}: ${Number.isInteger(load.sets) ? load.sets : load.sets.toFixed(1)} sets`
            : `${shape.muscle}: not worked`
          : undefined;
        return (
          <g
            key={`${shape.part}-${i}`}
            className={fill ? "body-map__muscle body-map__muscle--worked" : "body-map__muscle"}
            style={fill ? { fill } : undefined}
          >
            {title && <title>{title}</title>}
            {shape.points.map((points) => (
              <polygon key={points} points={points} />
            ))}
          </g>
        );
      })}
    </g>
  );
}

export function BodyMuscleMap({ muscles }: { muscles: MuscleLoad[] }) {
  const loads = new Map(muscles.map((m) => [m.muscle, m]));
  return (
    <figure className="body-map">
      <svg
        viewBox="0 0 220 200"
        role="img"
        aria-label="Muscles worked, front and back, darker orange for harder work"
      >
        <Figure label="Front" shapes={FRONT_SHAPES} loads={loads} />
        <g transform="translate(120 0)">
          <Figure label="Back" shapes={BACK_SHAPES} loads={loads} />
        </g>
      </svg>
      <figcaption className="body-map__legend" aria-hidden="true">
        <span>Light</span>
        <span className="body-map__ramp" />
        <span>Hard</span>
      </figcaption>
    </figure>
  );
}
