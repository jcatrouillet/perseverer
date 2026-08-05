import { Route, Switch } from "wouter";

import { AuthGate } from "./components/AuthGate";
import { isoDate } from "./dateUtils";
import { ActivityDetailPage } from "./pages/ActivityDetailPage";
import { ActivityListPage } from "./pages/ActivityListPage";
import { MonthView } from "./pages/calendar/MonthView";
import { WeekView } from "./pages/calendar/WeekView";
import { YearView } from "./pages/calendar/YearView";
import { FitnessPage } from "./pages/FitnessPage";
import { HealthPage } from "./pages/HealthPage";

function Today() {
  const today = new Date();
  return <MonthView year={today.getFullYear()} month={today.getMonth() + 1} />;
}

export function App() {
  return (
    <AuthGate>
      <nav>
        <a href="/">Calendar</a> · <a href="/activities">Activities</a> ·{" "}
        <a href="/fitness">Fitness &amp; Form</a> · <a href="/health">Health</a>
      </nav>
      <Switch>
        <Route path="/activities/:id">{(params) => <ActivityDetailPage id={params.id} />}</Route>
        <Route path="/activities" component={ActivityListPage} />
        <Route path="/fitness" component={FitnessPage} />
        <Route path="/health" component={HealthPage} />
        <Route path="/calendar/week/:date">
          {(params) => <WeekView date={params.date ?? isoDate(new Date())} />}
        </Route>
        <Route path="/calendar/:year/:month">
          {(params) => <MonthView year={Number(params.year)} month={Number(params.month)} />}
        </Route>
        <Route path="/calendar/:year">{(params) => <YearView year={Number(params.year)} />}</Route>
        <Route path="/" component={Today} />
        <Route>
          <main>
            <p>Not found.</p>
          </main>
        </Route>
      </Switch>
    </AuthGate>
  );
}
