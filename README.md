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

**See Perseverer in action (30 seconds):**

[![Play the Perseverer overview on YouTube](docs/media/perseverer-overview.jpg)](https://www.youtube.com/watch?v=07qXTe9qTJ8)

[▶ Watch on YouTube](https://www.youtube.com/watch?v=07qXTe9qTJ8)

![Week view](docs/screenshots/calendar-week.png)

- **Every data point, kept raw.** Every FIT file and API response is archived verbatim before it
  is parsed, and unknown fields are cataloged, never dropped. The whole database can be rebuilt
  from the archive without contacting any vendor.
- **Many sources, one timeline.** Garmin Connect, Garmin and Strava exports, Apple Health, a Eufy
  scale and the Kaya bouldering app, deduplicated into one history.
- **Your own analytics.** Fitness & Form, VO2max, race predictions, training zones, performance
  curves and records computed from your data with published models, shown next to Garmin's.
- **Plans that reach your watch.** Write workouts on the calendar and they appear on your Garmin.
- **Agent-ready.** 86 MCP tools let an AI assistant read everything and build a training plan.

---

## Features

Every sport a Garmin watch records is imported and shown: cycling, swimming, yoga, strength,
HIIT, skiing and the rest all appear on the calendar with their stats, maps and charts. The
deepest analysis is for **runs, rides, hikes and bouldering**, which get dedicated sections,
records and insights.

### Calendar

Day, week, month, year and all-time views, each with the stats that make sense at that scale.
The day view lists the day's activities with their maps, then that day's sleep stages, wellness
figures and an intraday body-battery chart. Weeks show sleep, steps, planned workouts and the
weather forecast for every day, followed by the week's stats (with the previous week for
comparison), Fitness & Form, compliance with the plan per sport, and a summary per sport. Months
and years add totals against the same period last year, streaks, activities by type, Fitness &
Form and health summaries.

| Week | Day |
|---|---|
| ![Week stats and compliance](docs/screenshots/calendar-week-stats.png) | ![Day view](docs/screenshots/calendar-day.png) |

| Month | Year |
|---|---|
| ![Month view](docs/screenshots/calendar-month.png) | ![Year view](docs/screenshots/calendar-year.png) |

The year view can also make **posters** of the year, drawn in the background and downloaded as
PNGs: a grid of every GPS trace (one sport or all), a heatmap around a point you pick on a map
(by default your home, with a range that holds at least half of your activities), a calendar
coloured by sport, and a circular view with one bar per activity, as long as its distance, with
races in their own colour.

Runs, rides, hikes and bouldering each get their own section in the period summaries (week,
month, year and all time):

- **Running:** distance per day, trailing totals, pace versus distance, time of day, a calendar
  heatmap and personal records for the period.
- **Cycling** (week, month, year and all time, only when the period has rides). The week adds an
  all-time record distance set that week, total distance and duration, fastest speed, share of a
  day, calories and elevation, and three charts over the last year (weekly distance, weekly best
  FTP, power against distance). Month, year and all time have: distance, number of
  rides, average speed, longest ride, max heart rate, average ride length, days ridden per
  week, most and least ridden weekdays, longest streak and break, distance per day, month or
  year, trailing distance (7 days
  for a month, 30 for a year, 365 for all time), average power against distance, and records at
  5, 10, 20, 40 and 100 km.
- **Hiking:** number of hikes, distance, time, average and maximum elevation gain, and featured
  hikes (longest, longest by time, most climbing, highest point reached) with their place names.
- **Bouldering:** sessions, climb time, routes, hardest grade sent, and routes attempted and
  completed per grade.

| Running | Hiking and bouldering |
|---|---|
| ![Running statistics for a month](docs/screenshots/calendar-month-stats.png) | ![Hiking and bouldering for a month](docs/screenshots/calendar-month-hikes.png) |

| Cycling, week | Cycling, month |
|---|---|
| ![Cycling summary for a week](docs/screenshots/calendar-week-cycling.png) | ![Cycling summary for a month](docs/screenshots/calendar-month-cycling.png) |

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
similar recent efforts, and the run insights flag records for the period ("longest run in the
last 12 months", "most calories burned", "slowest start in the last 90 days", "fastest finish
ever", "most negative km splits in the last 30 days", counting the second-half kilometres faster
than the first half's average pace). Rides get the same insights, compared with rides only
("longest ride", "fastest average speed", a ride streak).

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
the automatic matcher missed can be merged by hand, choosing field by field which source wins.
Every correction survives a full rebuild.

- **Tags:** mark an activity as a **race**, a **commute** or **virtual** from its page. Commutes
  can be left out of the Insights, and virtual activities hidden from the map. Some tags are
  applied for you: with your **home and work addresses** in Settings (optional), every activity
  that goes from one to the other is tagged as a commute, and any activity with **Zwift** in its
  title is tagged as virtual. A tag you set yourself always wins over the automatic one, and
  survives a rebuild.
- **Bike and tires:** a ride's page shows the bike it was made on (the one you chose, else the
  default for its type of ride) and lets you change it; the bike's tires then inherit the ride's
  distance (see Gear).

- **Duplicate detection:** activities recorded by two sources that were never merged (a Garmin
  recording and the same session from a Strava export, for example) are flagged, both on the
  activity page and in a list under Settings → Activities.
- **Trimming detection:** hikes and walks that end or start with a fast stretch, usually a drive
  with the recording still running, are flagged for review. Trimming recomputes distance, time,
  elevation and heart rate from the part you keep, without changing the original file, and can be
  undone.
- **Share links:** share one activity, or a whole week, month or year, through a link that opens
  without an account. The shared page shows the stats, charts, map and laps, never health data.

### Activity list and map

A filterable list of every activity with route thumbnails and each day's sleep, resting heart rate
and steps, and a map of everything you have ever recorded: one point per activity start,
coloured by sport family, with a legend naming the sports behind each colour.

| Activities | Map explorer |
|---|---|
| ![Activity list](docs/screenshots/activities-list.png) | ![Map explorer](docs/screenshots/map-explorer.png) |

The activity list filters by sport, a distance range and the race, virtual and commute tags; the
filters and the page live in the page address, so Back from an activity keeps them. On the map,
a "Hide virtual activities" toggle leaves the virtual ones off (it appears once you have tagged
some).

**Compare runs** (Activities → Compare, or "Compare" on a run's page) puts up to eight runs
side by side. Find runs by title, distance, date or the race, virtual and commute tags and add
them; each gets its own colour. Every run's title, date, distance, moving time, load and VDOT sit above charts of pace, grade-adjusted
pace, heart rate, cadence, elevation and power, on a shared distance or moving-time axis so one
tooltip reads every run at the same point. With a single run selected, it suggests your fastest
run of the same whole distance (any 10.x km run for a 10.02 km one). The selection lives in the
page address, so a comparison can be bookmarked.

The comparison also covers **rides** (the Running / Cycling switch on the page, or "Compare" on a
ride's page): a ride shows its average speed and power, the charts are speed, power, heart rate,
cadence (rpm) and elevation, and the suggestion is your fastest ride within 10% of its length.
Runs are only ever compared with runs, and rides with rides.

![Comparing four runs](docs/screenshots/compare-runs.png)

### Fitness & Form and health

Fitness (CTL), Fatigue (ATL) and Form (TSB) from your own training load, VO2max, HRV and lactate
threshold. Health trends cover weight and full body composition, resting and max heart rate,
SpO₂, respiration, blood pressure, steps, sleep and blood test results with your lab's own
reference ranges, all at week, month, year or all-time resolution.

| Fitness & Form | Health |
|---|---|
| ![Fitness and Form](docs/screenshots/fitness.png) | ![Health trends](docs/screenshots/health.png) |

### Insights

Running and cycling analytics computed from your own history:

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

A Running/Cycling switch at the top shows the same insights for rides, compared with rides only:
power trends (each ride's best 20 minutes against your FTP, with speed as a fallback for rides
without power), PR progress by average power, time in each power band, heart-rate zones with the
speed you hold in each, speed and power curves, and a rides Eddington number. For rides with
power, an FTP & VO2max tab tracks FTP, FTP/kg, Critical Power with W′ and a VO2max estimate, all
computed from your own power data. VO2max, race predictions and race readiness are running
models, so they only appear for running.

Settings → Personalize picks which sport the page opens on, and can add an **Exclude commutes**
toggle that leaves commute-tagged activities out of every figure. Each ride's page shows its max
speed next to the average, and its best average power over 5 s, 30 s, 1, 5, 20, 30 and
60 minutes. Rides have their own colour, on the map and everywhere else.

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

With the **Cycling** switch, the same tabs describe your rides:

| Power trends | FTP & VO2max |
|---|---|
| ![Cycling power trends](docs/screenshots/insights-cycling-power-trends.png) | ![FTP, Critical Power and VO2max](docs/screenshots/insights-cycling-ftp.png) |

| Training bands (by power) | PR progress (by power) |
|---|---|
| ![Time in each power band](docs/screenshots/insights-cycling-training-bands.png) | ![PR progress by average power](docs/screenshots/insights-cycling-pr-progress.png) |

### Planning and goals

- **Planned workouts on the calendar**, pushed to Garmin automatically within a week of their date
  and removed from Garmin once their day has passed, so the watch never fills up:
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
- **Goals** per week, month or year: as many distance goals and time goals as you like, one per
  sport (or all sports together), and bouldering sends by grade, each with a pace-to-target chart.
- **Compliance** per sport in the week view, matched automatically against recorded activities.
- **Notes** on any activity, day or week, written by you or by an AI agent through the API; the
  coming week's notes also appear in the weekly email.

### Gear and exercises

**Shoes:** mileage from your activities, a default pair per sport, and a replacement alert by
banner and email when a pair reaches its limit. Shoes are offered only for sports done in them
(running, walking, hiking, racket sports, gym training), not for rides, rowing, yoga and the like.

**Bikes and tires:**

- A bike has a brand, model, year, size and the distance already on it. It is chosen **per ride**
  (on the ride's page), or is the **default** for all cycling or for a type of ride: road,
  mountain, gravel, e-bike, eMTB or bike commute. The most specific default wins, and a default
  only counts rides from the moment you set it.
- Each bike can have a **front** and a **rear tire**, each with a brand, model, size, comment,
  distance already on it and a maximum distance. A tire inherits the distance of the rides made
  while it is installed, so a replacement, a swap to another bike or a retirement never charges old
  rides to the wrong tire. A bike may have no tires at all (a trainer bike, say).
- Tires can be replaced, taken off (kept as spares, with their distance) and fitted again, or
  retired. A tire at its limit raises the same banner and email as shoes.
- Adding a bike, shoes or a tire opens as a popup, so the page stays short.

Also here: a browsable library of every exercise the strength planner supports, with photos and
step-by-step instructions.

| Gear | Exercise library |
|---|---|
| ![Gear](docs/screenshots/gear.png) | ![Exercise library](docs/screenshots/exercises.png) |

| Bikes and tires |
|---|
| ![Bikes with front and rear tires, defaults per type of ride](docs/screenshots/gear-bikes.png) |

### Settings and reports

- **External tools:** connect Garmin, Kaya and the Eufy scale and sync them on demand, publish the
  calendar feed, and turn on the email summaries.
- **Activities:** bulk import a Garmin or Strava "export your data" archive (.zip; a "How do I get
  this file?" popup gives each vendor's steps) to bring in
  your full history (Apple Health from the CLI), rebuild the whole database from the raw archive,
  and review the activities flagged as possible duplicates or as needing a trim.
- **Personalize:** week start, 12/24-hour time, kilometres or miles, starting page (week, month, day,
  activity list, or the last activity imported), the sport Insights opens on, the "Exclude
  commutes" toggle, light or dark theme.
- **Commute:** optionally, your home and work addresses, so commutes are spotted automatically (an
  activity that starts near one and ends near the other, within 400 m). The card says why it asks:
  nothing else uses them, they stay on your server, and the address text is sent once to
  OpenStreetMap's Nominatim search service to find where it is. Your own tags always win.
- **Profile and physical profile:** home location and time zone (for forecasts), birthdate,
  height and sex (for estimates before real data exists), heart-rate zones, and your threshold
  pace, which switches running load to a pace-based TSS.
- **API access:** create an API key and read the API reference from the Profile tab.
- **Forgotten password:** "Forgot your password?" on the login page emails a one-time link, valid
  for an hour, to the address in your profile, where you choose a new password (needs the SMTP
  settings used for the email summaries).
- **A calendar feed** of your plan for Google Calendar.
- **Weekly and monthly email summaries**, sent in your own local time: the weekly on Sunday at
  20:00, after a fresh Garmin sync, and the monthly on the month's last day at 18:00. They mirror
  the Week and Month views: the figures with the previous period beside each, load and fitness,
  compliance with the plan, Running and Cycling sections with records and weekly distance bars,
  sleep and steps, and the coming week's plan, forecast and notes.

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

- A **REST API** (160 endpoints) with a per-athlete API key or password login. Documented in
  [docs/API.md](docs/API.md) and served by the app at `/api-docs.html`.
- An **MCP server** at `/mcp` with 86 tools, including writing workouts, races, goals and notes.
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
| [docs/DESIGN.md](docs/DESIGN.md) | Graphic charter: colours, typography, icons, components, charts, writing |
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
