// Day view's per-activity route animation: "there's no dot running along the path of the
// activity, like with the play button in the activity view. The animation should be played
// once when displayed." A compact, auto-starting sibling of ActivityRoute.tsx's own player --
// no scrubber, no manual play/pause button, no splits table or export buttons, since a card in
// a day's activity list isn't the activity detail page. Reuses ActivityRouteMap as-is, so the
// same start(green)/finish(checkered) markers and pace-gradient trace from the activity detail
// page's map show up here too, with no separate marker code needed.
import { useEffect, useRef, useState } from "react";

import { useActivityStream } from "../api/queries";
import { ANIMATION_DURATION_MS, buildRouteData } from "./ActivityRoute";
import { ActivityRouteMap } from "./ActivityRouteMap";

export function DayViewActivityRoute({ activityId }: { activityId: string }) {
  const stream = useActivityStream(activityId, true, "high");
  const [progress, setProgress] = useState(0);
  const hasPlayedRef = useRef(false);

  const route = stream.data ? buildRouteData(stream.data) : null;
  const hasRoute = route != null && route.points.length >= 2;

  useEffect(() => {
    if (!hasRoute || hasPlayedRef.current) return undefined;
    hasPlayedRef.current = true;
    let raf = 0;
    const startTime = performance.now();
    const step = (now: number) => {
      const p = (now - startTime) / ANIMATION_DURATION_MS;
      if (p >= 1) {
        setProgress(1);
        return;
      }
      setProgress(p);
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [hasRoute]);

  if (!hasRoute) return null;

  const markerIndex = Math.round(progress * (route.points.length - 1));

  return (
    <div className="day-view-route">
      <ActivityRouteMap
        points={route.points}
        distanceM={route.distanceM}
        elapsedS={route.elapsedS}
        highlightRange={null}
        markerIndex={markerIndex}
      />
    </div>
  );
}
