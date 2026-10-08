# Perseverer

**The goal of Perseverer is to replace Garmin Connect, Strava and intervals.icu with a single app
that you own.** Instead of spreading your training across three services, each holding part of
your history behind its own account, Perseverer brings it together in one place:

- **Instead of Garmin Connect:** your activities, sleep, HRV, body battery and daily health
  metrics, and structured workouts and courses sent to your watch.
- **Instead of Strava:** a complete activity history with maps, records, insights, recaps and
  share links, including everything from your Strava export.
- **Instead of intervals.icu:** Fitness & Form (CTL/ATL/TSB), training zones, performance
  curves, race predictions, goals, and a calendar where you plan workouts in a text syntax.

Your watch still uploads to Garmin Connect, and Perseverer syncs from it in the background: you
keep the device ecosystem but no longer need the three apps to see, analyse or plan your training.

*About the name:* Strava takes its name from the Swedish verb *att sträva*, "to strive". Perseverer
follows the same idea in French, the author's language: *persévérer* means "to persevere".

**Own your training data.** Perseverer is a self-hosted fitness and health platform: it pulls
everything your Garmin watch, your smart scale and your climbing logbook record, keeps the
original files forever, and turns them into a fast calendar-centred web app, a REST API and an
MCP server an AI coach can read and plan with.

![Week view](docs/screenshots/calendar-week.png)

- **Every data point, kept raw.** Every FIT file and API response is archived verbatim before it
  is parsed, and unknown fields are cataloged, never dropped. The whole database can be rebuilt
  from the archive without contacting any vendor.
- **Many sources, one timeline.** Garmin Connect, Garmin and Strava exports, Apple Health, a Eufy
  scale and the Kaya bouldering app, deduplicated into one history.
- **Your own analytics.** Fitness & Form, VO2max, race predictions, training zones, performance
  curves and records computed from your data with published models, shown next to Garmin's.
- **Plans that reach your watch.** Write workouts on the calendar and they appear on your Garmin.
- **Agent-ready.** 74 MCP tools let an AI assistant read everything and build a training plan.

---

## Features

### Calendar

Day, week, month, year and all-time views, each with the stats that make sense at that scale.
The day view adds that day's sleep stages, wellness figures and an intraday body-battery chart.
Weeks show sleep, steps, planned workouts and the weather forecast for every day; months and years
add totals against the same period last year, streaks, activities by type, running and climbing
breakdowns, Fitness & Form and health summaries.

| Month | Year |
|---|---|
| ![Month view](docs/screenshots/calendar-month.png) | ![Year view](docs/screenshots/calendar-year.png) |

Running gets its own section in every period: distance per day, trailing totals, pace versus
distance, time of day, a calendar heatmap and personal records for the period.

![Running statistics for a month](docs/screenshots/calendar-month-stats.png)

### Activities

Every activity has a detail page: weather at the time, pace variability, distance, heart rate and
elevation stats, training effect, the place name, the route on a map with a replay (exportable as
an image or an animated GIF), and a per-km split table with grade-adjusted pace. Activities from several sources are merged into one, with the evidence shown
and a one-click split if the merge was wrong.

| Run | Route and splits |
|---|---|
| ![Run detail](docs/screenshots/activity-run.png) | ![Route map and km splits](docs/screenshots/activity-route.png) |

Per-second charts for elevation, pace, grade-adjusted pace, heart rate, cadence and power, with the
planned workout's steps shaded behind them.

![Activity charts](docs/screenshots/activity-charts.png)

Structured workouts are compared lap by lap with what was planned. Each run is ranked against
similar recent efforts and the run insights flag records ("longest run in the last 12 months",
"most calories burned", "coldest run in the last 90 days").

| Intervals vs. plan | Recent efforts, zones, intervals |
|---|---|
| ![Intervals compared to the plan](docs/screenshots/activity-intervals.png) | ![Similar efforts and time in zones](docs/screenshots/activity-similar.png) |

Hikes and bouldering get their own treatment. Bouldering sessions combine Garmin's heart rate and
timing with the route list from the Kaya app: names, grades, attempts and sends. You can add a
note to a route, and it shows again whenever you repeat that route. Efforts the watch recorded
that Kaya doesn't have are listed as "Watch only" and can be removed from the route list.

| Hike | Bouldering |
|---|---|
| ![Hike detail](docs/screenshots/activity-hike.png) | ![Bouldering routes](docs/screenshots/activity-bouldering.png) |

You can correct what the devices got wrong: sport, race flag, title, trim a forgotten-stop tail,
log fueling, assign shoes, fix a climbing route's grade or add a route the watch missed. Duplicates
the automatic matcher missed can be merged by hand, choosing field by field which source wins;
Settings lists likely duplicates and activities that probably need trimming. Every correction
survives a full rebuild.

### Activity list and map

A filterable list of every activity with route thumbnails and each day's sleep, resting heart rate
and steps, and a map of everything you have ever recorded.

| Activities | Map explorer |
|---|---|
| ![Activity list](docs/screenshots/activities-list.png) | ![Map explorer](docs/screenshots/map-explorer.png) |

### Fitness & Form and health

Fitness (CTL), Fatigue (ATL) and Form (TSB) from your own training load, VO2max, HRV and lactate
threshold. Health trends cover weight and full body composition, resting and max heart rate,
SpO₂, respiration, blood pressure, steps, sleep and blood test results with your lab's own
reference ranges, all at week, month, year or all-time resolution.

| Fitness & Form | Health |
|---|---|
| ![Fitness and Form](docs/screenshots/fitness.png) | ![Health trends](docs/screenshots/health.png) |

### Insights

Running analytics computed from your own history:

- **Pace trends** and **training bands**: every run's VDOT over time, and how much you run at each
  intensity.
- **PR progress**: your pace record at every distance, with where this year beats last year.
- **Race predictions** and **VO2max**: 5K to marathon times from a trailing VDOT, with the runs
  driving the value.
- **Race readiness**: whether your weekly volume and long runs match your next race.
- **Pace/HR zones**: five training zones built from your best race and real heart-rate data.
- **Performance curve**: your best sustained pace or heart rate for every duration from 1 s to
  2 h.
- **Eddington number**: the largest *E* with *E* runs of at least *E* km, per year.

| Race readiness | Pace & heart-rate zones |
|---|---|
| ![Race readiness](docs/screenshots/insights-race-readiness.png) | ![Training zones](docs/screenshots/insights-zones.png) |

| Pace trends | PR progress |
|---|---|
| ![Pace trends](docs/screenshots/insights-pace-trends.png) | ![PR progress](docs/screenshots/insights-pr-progress.png) |

| VO2max | Race predictions |
|---|---|
| ![VO2max](docs/screenshots/insights-vo2max.png) | ![Race predictions](docs/screenshots/insights-race-predictions.png) |

| Performance curve | Eddington number |
|---|---|
| ![Performance curve](docs/screenshots/insights-performance-curve.png) | ![Eddington number](docs/screenshots/insights-eddington.png) |

### Planning and goals

- **Planned workouts on the calendar**, pushed to Garmin automatically within a week of their date:
  - **Running** uses a compact text syntax with a live preview: paces, heart-rate or zone targets,
    cadence, repeats, and lap-button steps.
  - **HIIT and strength** use 1,527 Garmin exercises with sets, reps and weights.
  - **Yoga and bouldering** are timed sessions.
  - A step-by-step builder for running workouts if you'd rather not type the syntax, and comments
    on a whole workout (sent to the watch) or on individual steps.
  - **Copy and paste across the calendar:** copy any planned workout (steps, exercises, time,
    duration and comments) or the structure of a completed run, and paste it onto another day.
  - Several workouts per day, recurring workouts, and a GPX route that is sent to the watch as a
    private course.
- **Races** with goal times, compared with the predicted finish.
- **Goals** per week, month or year: distance, time spent in any sport, and bouldering sends by
  grade, each with a pace-to-target chart.
- **Compliance** per sport in the week view, matched automatically against recorded activities.
- **Notes** on any activity, day or week, written by you or by an AI agent through the API; the
  coming week's notes also appear in the weekly email.

### Gear and exercises

Shoe mileage from your activities, a default pair per sport, replacement alerts by banner and
email. A browsable library of every exercise the strength planner supports, with photos and
step-by-step instructions.

| Gear | Exercise library |
|---|---|
| ![Gear](docs/screenshots/gear.png) | ![Exercise library](docs/screenshots/exercises.png) |

### Settings, sharing and reports

- **External tools:** connect Garmin, Kaya and the Eufy scale from the Settings page, sync on
  demand, upload Garmin or Strava exports (Apple Health from the CLI), or rebuild the whole
  database from the archive.
- **Personalize:** week start, 12/24-hour time, kilometres or miles, starting page (week, month, day,
  activity list, or the last activity imported), light or dark theme.
- **Profile and physical profile:** home location and time zone (for forecasts), birthdate,
  height and sex (for estimates before real data exists), heart-rate zones, and your threshold
  pace, which switches running load to a pace-based TSS.
- **Share links** for one activity or one period, readable without an account.
- **A calendar feed** of your plan for Google Calendar.
- **Weekly and monthly email summaries.**

| External tools | Personalize |
|---|---|
| ![External tools](docs/screenshots/settings-external.png) | ![Personalize](docs/screenshots/settings-personalize.png) |

### Mobile

The app is responsive and installable as a PWA.

<p>
  <img src="docs/screenshots/mobile-week.png" alt="Week view on a phone" width="260">
  &nbsp;
  <img src="docs/screenshots/mobile-activity.png" alt="Activity on a phone" width="260">
</p>

### API and MCP server

- A **REST API** (133 endpoints) with a per-athlete API key or password login. Documented in
  [docs/API.md](docs/API.md) and served by the app at `/api-docs.html`.
- An **MCP server** at `/mcp` with 74 tools, including writing workouts, races, goals and notes.
  Connect it to Claude Code with an API key, or to claude.ai as a custom connector over OAuth.

---

## Data sources

| Source | How | Data |
|---|---|---|
| Garmin Connect | Daily sync (token-based, rate-limited) | Activities, sleep, HRV, readiness, training status, VO2max, race predictions, lactate threshold, body battery, stress, steps, hydration |
| Garmin "Export your data" | Import a `.zip` | Full history of activities and wellness |
| Strava export | Import a `.zip` | Activities (FIT, GPX, TCX) |
| Apple Health export | Import `export.xml` | Blood pressure, historical weight and body composition |
| Eufy smart scale | Daily sync | Weight and body composition |
| Kaya | Daily sync | Bouldering routes, grades, attempts and sends |
| Any FIT files | Import or watch a folder | Activities and health files |

Garmin Connect and Kaya are reached through unofficial clients. Perseverer is careful with them:
it rate-limits, stops at the first "too many requests" response, and never stores your password.
These clients can still break when a vendor changes something.

## Quick start

Requirements: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 22+.

```bash
cp perseverer.env.example perseverer.env   # the one config file: set the two secrets
uv sync
uv run alembic upgrade head       # creates data/perseverer.db and the first athlete
uv run sync athlete set-password  # choose your login
uv run uvicorn perseverer.api.main:app --port 8000
```

In a second terminal:

```bash
cd frontend && npm install && npm run dev
```

Open <http://localhost:5173> and sign in. Bring data in from **Settings → External tools**
(Garmin login, export upload), or from the command line:

```bash
uv run sync auth login                          # Garmin Connect, MFA-capable
uv run sync import garmin-connect               # recent days
uv run sync import garmin-export ~/garmin.zip   # full history
```

All configuration, dev and production alike, lives in that one `perseverer.env`. To run the
whole stack in containers, use `docker compose --env-file perseverer.env up --build` (or
`podman compose ...`). On a production server, `scripts/install-production.sh` sets everything up
from the same file; see [docs/DEPLOY.md](docs/DEPLOY.md).

## Documentation

| Document | Contents |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, storage, ingestion, analytics, API, MCP, security, design decisions |
| [docs/DEPLOY.md](docs/DEPLOY.md) | Dev setup, production deployment, operations |
| [docs/API.md](docs/API.md) | Every REST endpoint, parameter and response |
| [docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md) | Every table, column and metric key, and where it comes from |
| [AGENTS.md](AGENTS.md) | Rules and conventions for contributors and coding agents |

## Tech stack

Python 3.12, FastAPI, SQLAlchemy Core, Alembic, SQLite (WAL), Parquet/PyArrow, DuckDB, APScheduler,
the official MCP SDK · React 19, TypeScript, Vite, TanStack Query, Recharts, Leaflet + MapLibre ·
nginx, Podman/Docker, systemd Quadlet, GitHub Actions, GHCR.

## License

[Apache License 2.0](LICENSE). Perseverer is an independent project, not affiliated with or
endorsed by Garmin, Strava or intervals.icu; their names are used only to describe what it
replaces. You may use, modify and redistribute Perseverer, including
commercially, provided you keep the copyright and license notices, ship the [NOTICE](NOTICE) file
with any redistribution or derivative work (crediting the original project), and state the files
you changed.
