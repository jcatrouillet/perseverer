import { lazy, Suspense } from "react";
import { Link, Route, Switch, useLocation } from "wouter";

import { AuthGate } from "./components/AuthGate";
import { GearAlertBanner } from "./components/GearAlertBanner";
import { Icon, type IconName } from "./components/Icon";
import { LoadingSpinner } from "./components/LoadingSpinner";
import { LogoutButton } from "./components/LogoutButton";
import { localIsoDate } from "./dateUtils";
import { usePersonalize, PersonalizeProvider } from "./PersonalizeContext";
import "./styles/gear.css";
import { MonthView } from "./pages/calendar/MonthView";
// Also statically imported: this is what the default "/" route (CurrentWeek() below) renders, so
// lazy-wrapping it would mean a loading-spinner flash on literally every login/app-open -- unlike
// the routes below, it genuinely IS needed for the default route's first paint. Pulls its own
// dependencies (ActivityCard, WeekRunningStats, WeekWellnessCharts, etc.) into the main chunk too
// -- confirmed via a real build this brings it to ~520kB, still well inside the ~2MB headroom the
// lazy-loading pass below bought back under vite-plugin-pwa's precache limit, not a razor's edge
// like the PlannedRaceForm/main-bundle situation that pass was originally fixing.
import { WeekView } from "./pages/calendar/WeekView";

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
const GearPage = lazy(() => import("./pages/GearPage").then((m) => ({ default: m.GearPage })));
const HealthPage = lazy(() =>
  import("./pages/HealthPage").then((m) => ({ default: m.HealthPage })),
);
const MapExplorerPage = lazy(() =>
  import("./pages/MapExplorerPage").then((m) => ({ default: m.MapExplorerPage })),
);
const SettingsPage = lazy(() =>
  import("./pages/SettingsPage").then((m) => ({ default: m.SettingsPage })),
);

// The default "/" landing route -- the athlete's own Personalize "starting page" preference
// (default: the current week, not the current month -- a week is the granularity an athlete
// actually plans and reviews training at day-to-day, and it's what most benefits from being one
// click away on login rather than requiring a nav click every time). Month/Day/Activities are
// each still a real, valid choice; Day and Activities are normally lazy-loaded specifically to
// keep them off the default route's own first paint (see the lazy() comment below) -- choosing
// either here reintroduces that one Suspense flash for that one athlete, already covered by the
// outer <Suspense> in App() below, same as every other lazy route.
function DefaultLandingPage() {
  const { default_view: defaultView } = usePersonalize();
  const today = new Date();
  if (defaultView === "month") {
    return <MonthView year={today.getFullYear()} month={today.getMonth() + 1} />;
  }
  if (defaultView === "day") {
    return <DayViewPage date={localIsoDate(today)} />;
  }
  if (defaultView === "activities") {
    return <ActivityListPage />;
  }
  return <WeekView date={localIsoDate(today)} />;
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
      <PersonalizeProvider>
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
            <NavLink href="/gear" icon="shoe">
              Gear
            </NavLink>
            <NavLink href="/exercises" icon="dumbbell">
              Exercises
            </NavLink>
            <NavLink href="/settings" icon="settings">
              Settings
            </NavLink>
          </div>
          <div className="app-nav__actions">
            <LogoutButton />
          </div>
        </nav>
        <GearAlertBanner />
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
              <Route path="/gear" component={GearPage} />
              <Route path="/exercises" component={ExerciseLibraryPage} />
              <Route path="/settings" component={SettingsPage} />
              <Route path="/day/:date">{(params) => <DayViewPage date={params.date} />}</Route>
              <Route path="/calendar/week/:date">
                {(params) => <WeekView date={params.date ?? localIsoDate()} />}
              </Route>
              <Route path="/calendar/all" component={AllTimeView} />
              <Route path="/calendar/:year/:month">
                {(params) => <MonthView year={Number(params.year)} month={Number(params.month)} />}
              </Route>
              <Route path="/calendar/:year">
                {(params) => <YearView year={Number(params.year)} />}
              </Route>
              <Route path="/" component={DefaultLandingPage} />
              <Route>
                <p>Not found.</p>
              </Route>
            </Switch>
          </Suspense>
        </div>
      </PersonalizeProvider>
    </AuthGate>
  );
}
