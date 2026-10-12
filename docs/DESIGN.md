# Design

Perseverer's graphic charter: how the web app looks and why, and the rules every screen follows.
It describes what the code already does. When this document and the code disagree, the code
wins and this document gets fixed. Contributors (people and coding agents) follow it for every
UI change; see [AGENTS.md](../AGENTS.md).

- [Principles](#principles)
- [Colour](#colour)
- [Typography](#typography)
- [Spacing, radius and elevation](#spacing-radius-and-elevation)
- [Icons](#icons)
- [Components](#components)
- [Charts](#charts)
- [Layout and responsiveness](#layout-and-responsiveness)
- [Mobile](#mobile)
- [Light and dark themes](#light-and-dark-themes)
- [Units, numbers and dates](#units-numbers-and-dates)
- [Writing](#writing)
- [Email and share pages](#email-and-share-pages)
- [Checklist for a UI change](#checklist-for-a-ui-change)

## Principles

1. **A colour means a metric or a sport family. There is no decorative colour.** Heart rate is
   always the same red, pace always the same blue, on a tile, a chip, a chart line or a legend.
   This keeps the palette from growing with every new page.
2. **The data is the headline.** Large tabular numerals, small quiet labels. Emphasis is rare so
   it still means something: at most three "hero" tiles per card.
3. **Tokens, never literals.** Every colour, space, radius and font size comes from a CSS
   variable in [`theme.css`](../frontend/src/styles/theme.css). A component that hardcodes a hex
   value breaks the other theme.
4. **One component per job.** A stat is a `StatTile`, a trend is a `TrendChart`, a metric's
   colour and icon come from `metricStyle.ts`. Reuse before adding.
5. **Dense but calm.** A training log has a lot of numbers; cards, small type for secondary
   facts and generous gaps between groups (not inside them) keep it readable.

## Colour

All tokens live in [`frontend/src/styles/theme.css`](../frontend/src/styles/theme.css), defined
once for the dark theme and overridden for the light one.

### Surfaces and text

| Token | Dark | Light | Use |
|---|---|---|---|
| `--color-bg` | `#12151c` | `#f4f6f9` | Page background, input fields |
| `--color-surface` | `#1b1f29` | `#ffffff` | Cards |
| `--color-surface-raised` | `#232836` | `#f4f6f9` | Tooltips, hovered rows, badges |
| `--color-border` | `#2e3441` | `#dde2ea` | Card and input borders, chart grid |
| `--color-text` | `#eef1f6` | `#1a1d24` | Body text, values |
| `--color-text-muted` | `#9aa3b5` | `#5a6273` | Labels, secondary lines, axes |
| `--color-text-faint` | `#6b7386` | `#8a92a3` | Hints, reference lines, quiet controls |
| `--color-accent` | `#4da3ff` | `#1a73e8` | Links, primary buttons, focus, the active tab |
| `--color-accent-strong` | `#7cb9ff` | `#0f5bc4` | Hover state of the accent |

### Data colours (tones)

Seven tones identify what a number is. `metricStyle.ts` maps every sport and health metric to
one of them; the `.tone-*` classes in `layout.css` bind `--tone` so a component only says which
tone it is.

| Tone | Token | Dark | Light | Metrics | Sport family |
|---|---|---|---|---|---|
| `pace` | `--color-pace` | `#4da3ff` | `#1a73e8` | pace, speed, distance, steps | road: running, cycling, rowing |
| `hr` | `--color-heart-rate` | `#ef5a6f` | `#d43a52` | heart rate, blood pressure | |
| `elevation` | `--color-elevation` | `#4caf7d` | `#2f9160` | elevation, SpO₂, water % | terrain: walking, hiking, snowshoeing, skiing |
| `power` | `--color-power` | `#b57bee` | `#8c4fd6` | power, weight | strength: strength training, HIIT, gym equipment |
| `cadence` | `--color-cadence` | `#3fb6c9` | `#1f92a3` | cadence, HRV, VO2max, sleep | mobility: yoga, breathing |
| `load` | `--color-load` | `#e8a33d` | `#c47f1a` | training load, calories, stress | skill: climbing, racket sports |
| `neutral` | `--color-text-muted` | | | anything without a tone | an unmapped sport |

Fourteen sports share five hues on purpose: a year's "activities by type" then reads as four or
five signals, not fourteen. A sport Garmin adds tomorrow renders in `neutral` with a generic icon
until someone gives it a family; nothing breaks.

When two metrics share a chart, they must have different tones (heart rate's max and resting
lines, HRV/SpO₂/stress, the two respiration rates). If both naturally have the same tone, the
chart overrides one series' colour with `toneColor(...)` of another tone, scoped to that chart.

### Status colours

`--color-success`, `--color-warning` and `--color-danger` answer "is this good or bad", a
different question from "which metric is this". Some share values with data tones today; they
stay separate tokens so changing one never changes the other. Use them for errors, alerts
(gear over its limit), out-of-range blood results and missed goals, never to colour a metric.

### Effort zones

`--color-zone-1` to `--color-zone-5` form an ordered cool-to-hot scale (teal, green, yellow,
orange, red) for heart-rate and pace zones and anything else that is a ranked intensity. Don't
use data tones for a scale: they are chosen to differ from each other, not to read in order.

### Tinted surfaces

Every tinted surface is a `color-mix()` of `--tone` with transparency, never a new colour:

- icon chip background: `color-mix(in srgb, var(--tone) 15%, transparent)`;
- hero tile: an 8% wash over the card (`color-mix(in srgb, var(--tone) 8%, var(--color-surface))`)
  and a 22% border;
- chips and pills: the same idea at their own percentage.

## Typography

- **Family:** the system UI stack (`--font-sans`: San Francisco, Segoe UI, Roboto, Helvetica,
  Arial). No web font: it loads instantly, offline too, and matches each device.
- **Scale:** `--font-size-sm` 0.8125rem (labels, secondary lines, tables, notes),
  `--font-size-base` 0.9375rem (body), `--font-size-lg` 1.125rem (`h2`, card headings),
  `--font-size-xl` 1.5rem (`h1`, page titles). Stat values use 1.5rem, weight 600.
- **Stat labels:** 0.6875rem, weight 700, uppercase, letter-spacing 0.075em, muted colour.
  Smaller and quieter than the figure they label.
- **Numbers:** `font-variant-numeric: tabular-nums` wherever figures line up (tables, tiles,
  split lists), so columns don't jitter.
- **Units** sit inside the value, smaller and muted (`84.3 km`), so the figure carries the line.

## Spacing, radius and elevation

| Token | Value | Typical use |
|---|---|---|
| `--space-1` | 0.25rem | icon-to-text inside a chip |
| `--space-2` | 0.5rem | label-to-value, small gaps |
| `--space-3` | 0.75rem | input padding, list rows |
| `--space-4` | 1rem | card padding, gaps between tiles |
| `--space-5` | 1.5rem | page padding, between sections of a card |
| `--space-6` | 2.5rem | large page sections |
| `--radius-sm` | 4px | icon chips, small marks |
| `--radius-md` | 8px | buttons, inputs, tooltips |
| `--radius-lg` | 14px | cards |
| `--shadow-card` | | every card, softer in light theme |

Groups are laid out with flex or grid and `gap`. Stacked cards get `--space-4` between them
(`.card + .card`); cards inside a grid rely on the grid's own gap.

## Icons

[`Icon.tsx`](../frontend/src/components/Icon.tsx) is the only icon source.

- **Metric, navigation and control icons** are a hand-drawn sprite: stroke only, `currentColor`,
  1.6 stroke width, round caps and joins, sized `1em` by CSS so text size sets icon size.
- **Sport pictograms** (`run`, `walk`, `hike`, `bike`, `dumbbell`, `yoga`, `climb`, `snow`,
  `racket`, `waves`) are Phosphor "fill" icons, rendered with `icon--filled`.
- **Weather** icons follow the WMO weather-code groups (`sun`, `cloud`, `rain`, `fog`, `storm`).
- An icon next to a metric sits in an **icon chip**: a 22px rounded square tinted with the
  metric's tone (`.icon-chip`, with `--lg` 44px for activity cards and `--xl` 64px beside a block
  of text such as the weather readout).
- Add a glyph to the sprite only when no existing one fits; reuse the closest one otherwise.
- Decorative icons are `aria-hidden`; an icon-only button gets an `aria-label`.

## Components

Use these before writing new markup. All live in `frontend/src/components/`.

| Component / class | Use |
|---|---|
| `.card` | Every page section: surface, border, `--radius-lg`, `--space-4` padding, a heading `h2`. |
| `StatTile` | Any single figure: label with icon chip, value, optional unit and `meta` line (e.g. "41.6 km previous week"). `hero` adds a wash of its tone and a coloured numeral; at most three per card. |
| `.stat-grid` | The responsive grid that holds stat tiles. |
| `MetricChip` / `.chip` | A compact value with its icon in its tone (activity cards, day strips). |
| `.badge` | A small neutral pill for a status or count ("Done", "Draft"). |
| `.button`, `.button--primary`, `.button--full` | Buttons. One primary action per form. |
| `.field` + `.input` | A labelled form field; focus shows the accent border and a soft ring. |
| `MetricExplorer` | The list-plus-detail shell of the Fitness, Health and Insights pages: a grouped, collapsible metric list on the left and the selected chart on the right; a vertical accordion on a phone. |
| `TrendControls` | Week / Month / Year / All-time plus previous and next, with a fixed-width label so the arrows never move. |
| `TrendChart` | Any metric over time: one or two Y axes, optional area fill, reference lines. |
| `ChartLegend` | The legend under a chart (plain HTML, not Recharts' own). |
| `ChartFullscreen` | On a phone, a chart's title opens the chart full screen. |
| `Modal` | Dialogs and lightboxes. |
| `LoadingSpinner` | Every loading state. |
| `TimeOfDayField` | Every time-of-day input (honours the 12/24-hour setting; never a native time input). |
| `.table-scroll` | Wraps any table wider than a phone, so it scrolls inside its card. |

Patterns:

- **Reveal in place.** Adding a note, a blood result or a route opens a form where the "+ Add"
  link was, rather than a modal. Lists of many items collapse into `<details>`.
- **Empty states** say what is missing and how to get it; an empty card is not shown at all
  when there is nothing useful to say.
- **Loading** shows `LoadingSpinner`; errors show a short `role="alert"` message in the card.

## Charts

All charts are Recharts components (or, for the share pages, hand-built SVG) styled with the
same tokens.

- **Colour:** each series takes its metric's tone through `toneColor()` from `metricStyle.ts`,
  never a colour picked by array index. Zones use the zone scale. Status colours mark
  out-of-range points (blood results outside the lab's range are larger red dots).
- **Grid and axes:** grid `--color-border` dashed `3 3`; axes and ticks `--color-text-muted`,
  11px; tick labels use the series' own formatter (paces as `5:12`, durations as `1:05:00`).
- **Reference lines** (targets, thresholds, zero): `--color-text-faint`, dashed `4 4`, with
  `ifOverflow="extendDomain"` so a line outside the data's range is still drawn.
- **Tooltip:** `--color-surface-raised` background, `--color-border` border, 8px radius, values
  formatted with the series' formatter.
- **Pace axes are reversed:** faster at the top, as runners read pace.
- **Missing data is a gap,** never a zero. Buckets without a reading are `null`.
- **Legend:** `ChartLegend` below the plot, swatches in the series colours.
- **Size:** `ResponsiveContainer` at full card width; a chart's height is fixed per chart, so the
  page doesn't jump while data loads.

## Layout and responsiveness

- The app column is at most **1080px** wide (`.app-main`), centred, with `--space-5` padding.
- Every page works at **375px** (a phone) without horizontal scrolling. Wide content scrolls
  inside its own container (`.table-scroll`, chart cards), never the page.
- Main breakpoints: **640px** (phone layout: stacked columns, chart titles open full screen) and
  **720px** for some multi-column cards. Grids prefer `auto-fill` / `minmax()` over breakpoints.
- Flex children that hold charts get `min-width: 0`, and a column layout uses
  `align-items: stretch`, so a chart never keeps a desktop width on a phone.
- The app is installable as a PWA; a phone layout is a first-class layout, not an afterthought.

## Mobile

Many athletes open Perseverer on a phone right after a run, often from the installed app, so a
phone layout is a design target of its own, not a shrunken desktop.

| Phone layout | Phone activity page |
|---|---|
| ![Week view on a phone](screenshots/mobile-week.png) | ![Activity on a phone](screenshots/mobile-activity.png) |

### What "phone" means

- **640px** is the phone breakpoint. CSS uses `@media (max-width: 640px)`; components that must
  render *different content* (not just hide or restyle it) use the `useIsMobile()` hook, which
  reads the same 640px query, so the two can never disagree.
- **720px** switches multi-column layouts to one column (the metric explorer, the week view).
- **A minimum width must never exceed the screen.** Write `min-width: min(320px, 100%)`, not
  `min-width: 320px`: on a 375px phone the plain value pushed the route map past its card.
- Pages with a dense form or grid add their own steps where they need them (gear: 760 and 520px;
  sign-in: 900, 700 and 520px). Prefer fluid grids (`auto-fill`, `minmax()`, `clamp()`) to a new
  breakpoint.
- Design and check at **375px** wide, the narrowest common phone.

### Patterns used on a phone

| Element | Desktop | Phone |
|---|---|---|
| Top navigation | One row of tabs with icons | Tabs wrap onto extra rows; no hamburger menu, every section stays one tap away |
| Date navigator | A 7-year window with ‹ › arrows | Every year in one row that scrolls sideways; the arrows are dropped |
| Week view | Seven day columns | Day columns at least 140px wide in a strip that scrolls sideways |
| Metric explorer (Fitness, Health, Insights) | Metric list beside the chart | The list stacks above the chart as collapsible groups |
| Charts | Inline at full card width | The chart's title becomes a button that opens it full screen (`ChartFullscreen`) |
| Tables (splits, routes, laps) | Full width | Scroll sideways inside the card (`.table-scroll`); the page itself never scrolls sideways |
| Modals | Centred panel | Nearly full screen, smaller margins, body scrolls inside |
| Forms | Fields side by side where it helps | One field per row |
| Route map | Map beside the splits table | Map above the table, with a full-screen button |

### Touch

- **Nothing important may depend on hover.** A phone has no hover, so information shown in a
  hover tooltip must also be reachable another way: a tap, a details line, or the page the item
  links to. Recharts tooltips open on tap; the PR-progress chart selects a dot on touch and shows
  its details in a strip below.
- **Known gaps:** the running heatmap's day tooltip is CSS `:hover` only (on a phone, a tap
  follows the link to that day instead), and a few rows reveal secondary details on hover. Fix
  these when the component is next touched rather than adding more.
- **Maps must not trap scrolling.** Small route thumbnails (activity cards, the day view) don't
  pan or zoom, so a swipe scrolls the page; the large route map is interactive and has a
  full-screen button.
- **Overlays on a map** (the pace legend, the credit line, zoom and full-screen buttons) must not
  cover each other at 375px. On a phone the route map's credit line is shrunk to one line and
  the legend sits above it.
- **Targets:** buttons and links keep the standard padding (`.button` is
  `--space-2` × `--space-3` on a 15px font), and small controls such as the routes table's ×
  sit apart from other controls. Don't make tap targets smaller to fit more on a row; let the
  row wrap.
- Text inputs use the right `type` and `autoComplete` (`username`, `current-password`,
  `new-password`) so phone keyboards and password managers behave.

### The installed app (PWA)

- Installable from the browser's "Add to home screen"; it opens **standalone** (no browser bar)
  with the dark background `#12151c` as its splash and status-bar colour, and its own icons
  (192px, 512px and a maskable 512px).
- The service worker precaches the app shell, so the app opens instantly and offline. Heavy
  pages and libraries (maps, the exercise catalogue) are lazy-loaded, and every precached file
  stays under 2 MB.
- New versions install in the background (`registerType: "autoUpdate"`); an open app switches to
  the new version on its next load, so after a deploy, closing and reopening the app (or one
  refresh) picks it up.
- The service worker never answers `/api/`, `/mcp`, `/share/`, `/oauth/`, the OAuth endpoints or
  `/.well-known/` from its cache (`navigateFallbackDenylist` in `vite.config.ts`, kept in step
  with nginx). Opening a share link or signing in an AI connector on a phone that has the app
  installed must reach the server.

### Checking a phone layout

- In the browser at 375 × 812 (and 390 wide), in light and dark: no sideways page scroll
  (`document.documentElement.scrollWidth` equals the viewport width), nothing clipped, charts
  sized to the screen.
- Tap through anything that has a hover effect on desktop.
- For a layout bug report from a real phone, also check the installed app after a refresh: a
  stale service-worker shell is the most common "it still looks old" cause.

## Light and dark themes

- Both themes are complete and equal. Every component styles only through tokens, so it works in
  both without a theme-specific rule.
- The theme follows the device (`prefers-color-scheme`) until the athlete picks one in
  Settings → Personalize; the choice is stored per device and applied by setting
  `data-theme` on the root element.
- Light-theme data colours are darker versions of the dark-theme ones, for contrast on white.
- Check every UI change in both themes. A "raised" surface must differ from the card under it in
  both (a past bug made tooltips invisible in light theme).

## Units, numbers and dates

- **Storage is SI** (metres, seconds, m/s); conversion happens only for display.
- Use the shared formatters, which apply the athlete's settings:
  [`formatDistance.ts`](../frontend/src/formatDistance.ts) (km or miles, paces per km or per
  mile, speeds) and [`formatTime.ts`](../frontend/src/formatTime.ts) (12- or 24-hour clock).
  Never write `distance_m / 1000` and a literal "km" in a component.
- **Paces** read `m:ss /km` (or `/mi`), durations `h:mm:ss` or `1h 5m`, distances with one
  decimal (`5.0 km`).
- **Dates:** "today" is the browser's local date (`dateUtils.ts::localIsoDate()`), and a date
  stored as UTC midnight is formatted with `timeZone: "UTC"`, or it shifts by a day west of UTC.
- The week starts on the athlete's chosen day in every calendar grid.

## Writing

- **Plain words from the athlete's side.** "Your password has been changed", not "Credential
  updated". Name things as people know them: "Sync now", "Starting page", "Watch only (not in
  Kaya)".
- **Sentence case** for headings, buttons and labels ("Run insights", "Send reset link").
- **Buttons say what happens:** "Set new password", "Send reset link", "Remove this route".
- **Errors say what went wrong and what to do**, without apologising: "This link is invalid,
  expired or already used. Ask for a new one from the log-in page."
- **Insights and records name their period:** "Fastest finish in the last 90 days", "Longest run
  ever", never a bare "Fastest finish".
- **Never invent a value.** Unknown is shown as "—" or the element is omitted; a zero is only
  shown when it is a real zero.
- Health information is described, never judged: no "normal" or "healthy" ranges beyond the
  ones from the athlete's own lab report.

## Email and share pages

These render outside the React app but follow the same charter:

- **Share pages** (`sharing.py`) reuse the app's tokens as CSS variables (dark by default, light
  via `prefers-color-scheme`), the same icon chips and tones as `StatTile`, and the same chart
  conventions (reversed pace axis, tone colours). They never show health data.
- **Emails** (`email_reports.py`, password reset) must survive email clients: inline styles only,
  a light palette, one table layout, no external resources, and **pixel widths** for bars
  (percentage widths collapse to zero inside table cells). Check them by rendering.

## Checklist for a UI change

- [ ] Colours come from tokens; a metric uses its tone from `metricStyle.ts`.
- [ ] Existing components are reused (`StatTile`, `TrendChart`, `.card`, `.button`, formatters).
- [ ] Distances, paces and times go through the shared formatters.
- [ ] Checked in the browser at desktop width and at 375px: no horizontal page scroll (see
      [Mobile](#mobile)).
- [ ] Nothing important is reachable only by hovering.
- [ ] Checked in light and dark themes.
- [ ] Keyboard focus is visible; icon-only buttons have an `aria-label`.
- [ ] Empty, loading and error states are handled.
- [ ] Copy follows [Writing](#writing).
