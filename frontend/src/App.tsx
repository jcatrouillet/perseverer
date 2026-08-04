import { Route, Switch } from "wouter";

import { AuthGate } from "./components/AuthGate";
import { ActivityDetailPage } from "./pages/ActivityDetailPage";
import { ActivityListPage } from "./pages/ActivityListPage";
import { CalendarPage } from "./pages/CalendarPage";

export function App() {
  return (
    <AuthGate>
      <Switch>
        <Route path="/activities/:id">{(params) => <ActivityDetailPage id={params.id} />}</Route>
        <Route path="/activities" component={ActivityListPage} />
        <Route path="/" component={CalendarPage} />
        <Route>
          <main>
            <p>Not found.</p>
          </main>
        </Route>
      </Switch>
    </AuthGate>
  );
}
