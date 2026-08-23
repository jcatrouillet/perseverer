// Same breakpoint modal.css's own `@media (max-width: 640px)` already uses for "this is
// phone-sized, not just a narrow browser window" -- kept as one shared constant/hook rather than
// each caller hardcoding its own media query string, so the breakpoint can't drift between them.
//
// Unlike ChartFullscreen.tsx's own mobile-only affordance (a pure CSS display:none toggle,
// deliberately avoiding a JS check there since only *visibility* needed to differ), DateNavigator
// genuinely needs this: which years are in the DOM at all differs between mobile (all of them,
// swipeable) and desktop (a paged 7-year window with prev/next arrows) -- not something CSS
// alone can express, since that's a difference in *data rendered*, not just what's shown/hidden.
import { useEffect, useState } from "react";

const MOBILE_QUERY = "(max-width: 640px)";

export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(() => window.matchMedia(MOBILE_QUERY).matches);

  useEffect(() => {
    const mql = window.matchMedia(MOBILE_QUERY);
    const onChange = () => setIsMobile(mql.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return isMobile;
}
