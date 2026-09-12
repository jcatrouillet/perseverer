import { lazy, Suspense } from "react";
import { Link, Route, Switch, useLocation } from "wouter";

import { AuthGate } from "./components/AuthGate";
import { Icon, type IconName } from "./components/Icon";
import { LoadingSpinner } from "./components/LoadingSpinner";
import { LogoutButton } from "./components/LogoutButton";
import { ThemeToggle } from "./components/ThemeToggle";
import { isoDate } from "./dateUtils";
import { MonthView } from "./pages/calendar/MonthView";
// Statically imported (not lazy) despite being routed elsewhere: MonthView/AllTimeView/YearView
// already import CORE_METRICS/HRV_METRIC/WEIGHT_METRIC from here, so it's pulled into the main
// chunk regardless -- lazy-wrapping it would only add a pointless Suspense flash with zero
// bundle-size benefit (confirmed via vite's own "ineffective dynamic import" build warning).
import { HealthPage } from "./pages/HealthPage";

// Lazily loaded: not needed for the default "/" route's first paint, and some (Map, Insights,
// Exercises) pull in heavy dependencies (maplibre-gl, recharts) that would otherwise bloat the
// main bundle past vite-plugin-pwa's 2MB single-file precache limit -- see ExerciseStepEditor.tsx's
// own preloadExerciseCatalog for the earlier instance of this same constraint.
const ActivityDetailPage = lazy(() =>
  import("./pages/ActivityDetailPage").then((m) => ({ default: m.ActivityDetailPage })),
);
const ActivityListPage = lazy(() =>
  import("./pages/ActivityListPage").then((m) => ({ default: m.ActivityListPage })),
);
const AllTimeView = lazy(() =>
  import("./pages/calendar/AllTimeView").then((m) => ({ default: m.AllTimeView })),
);
const WeekView = lazy(() =>
  import("./pages/calendar/WeekView").then((m) => ({ default: m.WeekView })),
);
const YearView = lazy(() =>
  import("./pages/calendar/YearView").then((m) => ({ default: m.YearView })),
);
const DayViewPage = lazy(() =>
  import("./pages/DayViewPage").then((m) => ({ default: m.DayViewPage })),
);
const ExerciseLibraryPage = lazy(() =>
  import("./pages/ExerciseLibraryPage").then((m) => ({ default: m.ExerciseLibraryPage })),
);
const FitnessPage = lazy(() =>
  import("./pages/FitnessPage").then((m) => ({ default: m.FitnessPage })),
);
const InsightsPage = lazy(() =>
  import("./pages/InsightsPage").then((m) => ({ default: m.InsightsPage })),
);
const MapExplorerPage = lazy(() =>
  import("./pages/MapExplorerPage").then((m) => ({ default: m.MapExplorerPage })),
);
const SettingsPage = lazy(() =>
  import("./pages/SettingsPage").then((m) => ({ default: m.SettingsPage })),
);

function Today() {
  const today = new Date();
  return <MonthView year={today.getFullYear()} month={today.getMonth() + 1} />;
}

function NavLink({
  href,
  icon,
  matches,
  children,
}: {
  href: string;
  icon: IconName;
  /** Extra route prefixes this tab owns, for sections reachable at more than one path.
   * Without it "Calendar" only highlighted on exactly "/" -- so browsing any actual calendar
   * page (/calendar/2026, /day/2026-08-07) left the whole nav bar with nothing selected. */
  matches?: string[];
  children: React.ReactNode;
}) {
  const [location] = useLocation();
  const isActive = (matches ?? [href]).some(
    (prefix) => location === prefix || (prefix !== "/" && location.startsWith(`${prefix}/`)),
  );
  return (
    <Link href={href} className={isActive ? "is-active" : ""}>
      <Icon name={icon} />
      {children}
    </Link>
  );
}

export function App() {
  return (
    <AuthGate>
      <nav className="app-nav">
        <div className="app-nav__links">
          <NavLink href="/" icon="grid" matches={["/", "/calendar", "/day"]}>
            Calendar
          </NavLink>
          <NavLink href="/activities" icon="list">
            Activities
          </NavLink>
          <NavLink href="/fitness" icon="trend">
            Fitness &amp; Form
          </NavLink>
          <NavLink href="/health" icon="heart">
            Health
          </NavLink>
          <NavLink href="/map" icon="map">
            Map
          </NavLink>
          <NavLink href="/insights" icon="bolt">
            Insights
          </NavLink>
          <NavLink href="/exercises" icon="dumbbell">
            Exercises
          </NavLink>
          <NavLink href="/settings" icon="settings">
            Settings
          </NavLink>
        </div>
        <div className="app-nav__actions">
          <ThemeToggle />
          <LogoutButton />
        </div>
      </nav>
      <div className="app-main">
        <Suspense fallback={<LoadingSpinner size="lg" />}>
          <Switch>
            <Route path="/activities/:id">
              {(params) => <ActivityDetailPage id={params.id} />}
            </Route>
            <Route path="/activities" component={ActivityListPage} />
            <Route path="/fitness" component={FitnessPage} />
            <Route path="/health" component={HealthPage} />
            <Route path="/map" component={MapExplorerPage} />
            <Route path="/insights" component={InsightsPage} />
            <Route path="/exercises" component={ExerciseLibraryPage} />
            <Route path="/settings" component={SettingsPage} />
            <Route path="/day/:date">{(params) => <DayViewPage date={params.date} />}</Route>
            <Route path="/calendar/week/:date">
              {(params) => <WeekView date={params.date ?? isoDate(new Date())} />}
            </Route>
            <Route path="/calendar/all" component={AllTimeView} />
            <Route path="/calendar/:year/:month">
              {(params) => <MonthView year={Number(params.year)} month={Number(params.month)} />}
            </Route>
            <Route path="/calendar/:year">
              {(params) => <YearView year={Number(params.year)} />}
            </Route>
            <Route path="/" component={Today} />
            <Route>
              <p>Not found.</p>
            </Route>
          </Switch>
        </Suspense>
      </div>
    </AuthGate>
  );
}
