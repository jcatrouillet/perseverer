// The Activities section's two views, as a tab strip at the top of each: the list and the run
// comparison. The top navigation's Activities tab stays highlighted on both (it owns every
// /activities/... path).
import { Link, useLocation } from "wouter";

const TABS = [
  { href: "/activities", label: "All activities" },
  { href: "/activities/compare", label: "Compare" },
];

export function ActivitiesTabs() {
  const [location] = useLocation();
  return (
    <nav className="activities-tabs" aria-label="Activities views">
      {TABS.map((tab) => {
        const active = location === tab.href;
        return (
          <Link
            key={tab.href}
            href={tab.href}
            className={`activities-tabs__tab${active ? " is-active" : ""}`}
            aria-current={active ? "page" : undefined}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
