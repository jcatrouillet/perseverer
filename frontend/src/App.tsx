import { Link, Route, Switch, useLocation } from "wouter";

import { AuthGate } from "./components/AuthGate";
import { Icon, type IconName } from "./components/Icon";
import { ThemeToggle } from "./components/ThemeToggle";
import { isoDate } from "./dateUtils";
import { ActivityDetailPage } from "./pages/ActivityDetailPage";
import { ActivityListPage } from "./pages/ActivityListPage";
import { AllTimeView } from "./pages/calendar/AllTimeView";
import { MonthView } from "./pages/calendar/MonthView";
import { WeekView } from "./pages/calendar/WeekView";
import { YearView } from "./pages/calendar/YearView";
import { DayViewPage } from "./pages/DayViewPage";
import { ExerciseLibraryPage } from "./pages/ExerciseLibraryPage";
import { FitnessPage } from "./pages/FitnessPage";
import { HealthPage } from "./pages/HealthPage";
import { InsightsPage } from "./pages/InsightsPage";
import { MapExplorerPage } from "./pages/MapExplorerPage";
import { SettingsPage } from "./pages/SettingsPage";

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
        <ThemeToggle />
      </nav>
      <div className="app-main">
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
      </div>
    </AuthGate>
  );
}
