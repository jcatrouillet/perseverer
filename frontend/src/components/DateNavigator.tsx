import { useEffect, useState } from "react";
import { Link, useLocation } from "wouter";

import { useActivityYears } from "../api/queries";
import {
  EARLIEST_PLAUSIBLE_DATE,
  isoWeekNumber,
  monthGridWeeks,
  monthName,
  monthRange,
  parseIsoDate,
} from "../dateUtils";
import { useIsMobile } from "../useIsMobile";

const YEARS_PER_WINDOW = 7;
const MONTHS = Array.from({ length: 12 }, (_, i) => i + 1);

/** The persistent year -> month -> week navigation bar shared by the year/month/week/day
 * views. The day grid is a real Monday-aligned mini calendar (one row per week): clicking the
 * week affordance at the start of a row goes to that week (`/calendar/week/:monday`); clicking
 * a specific day goes straight to that day (`/day/:date`). `selectedDate` highlights one day
 * cell (Day View); `selectedWeekStart` highlights an entire week row (Week View) instead --
 * kept as separate props rather than one, since a week's Monday and "the selected day" are
 * different things and conflating them mis-highlighted just the Monday cell for Week View. */
export function DateNavigator({
  year,
  month,
  selectedDate,
  selectedWeekStart,
}: {
  year: number;
  month?: number;
  selectedDate?: string;
  selectedWeekStart?: string;
}) {
  const [location] = useLocation();
  const isMobile = useIsMobile();
  const activityYears = useActivityYears();
  const availableYears = (() => {
    const years = new Set<number>();
    for (const y of activityYears.data ?? []) {
      if (y >= Number(EARLIEST_PLAUSIBLE_DATE.slice(0, 4))) {
        years.add(y);
      }
    }
    // The year currently being viewed always shows, even before data has loaded or if it
    // genuinely has zero activities yet (e.g. a brand new year), so the current page is never
    // missing from its own year list.
    years.add(year);
    return Array.from(years).sort((a, b) => b - a);
  })();

  // Index into `availableYears`, not a raw year number -- years with data aren't necessarily
  // contiguous, so "window - 7" arithmetic on the year value itself could land on a gap.
  const [windowIndex, setWindowIndex] = useState(0);
  useEffect(() => {
    const idx = availableYears.indexOf(year);
    if (idx !== -1 && (idx < windowIndex || idx >= windowIndex + YEARS_PER_WINDOW)) {
      setWindowIndex(idx);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [availableYears.join(","), year]);

  // Descending, most recent first (left) to oldest last (right) -- so "‹ newer" sits next to
  // the most recent year shown and "older ›" sits next to the oldest one, each arrow pointing
  // in the direction it actually moves the window.
  //
  // Mobile shows every available year with no paging arrows at all, rather than a 7-year window
  // -- the years row already scrolls horizontally there (layout.css), so paging buttons next to
  // a swipeable strip are redundant, not a second way to reach the same years.
  const years = isMobile
    ? availableYears
    : availableYears.slice(windowIndex, windowIndex + YEARS_PER_WINDOW);
  const { start: monthStart, end: monthEnd } = month
    ? monthRange(year, month)
    : { start: "", end: "" };
  const weeks = month ? monthGridWeeks(year, month) : [];

  return (
    <nav className="date-nav">
      <div className="date-nav__row date-nav__row--years">
        <Link
          href="/calendar/all"
          className={`date-nav__all-time${location === "/calendar/all" ? " is-active" : ""}`}
        >
          All
        </Link>
        {!isMobile && (
          <button
            type="button"
            className="date-nav__page"
            onClick={() => setWindowIndex((i) => Math.max(0, i - YEARS_PER_WINDOW))}
            disabled={windowIndex === 0}
            aria-label="Show newer years"
          >
            ‹
          </button>
        )}
        {years.map((y) => (
          <Link
            key={y}
            href={`/calendar/${y}`}
            className={y === year && location !== "/calendar/all" ? "is-active" : ""}
          >
            {y}
          </Link>
        ))}
        {!isMobile && (
          <button
            type="button"
            className="date-nav__page"
            onClick={() =>
              setWindowIndex((i) =>
                Math.min(
                  Math.max(0, availableYears.length - YEARS_PER_WINDOW),
                  i + YEARS_PER_WINDOW,
                ),
              )
            }
            disabled={windowIndex + YEARS_PER_WINDOW >= availableYears.length}
            aria-label="Show older years"
          >
            ›
          </button>
        )}
      </div>

      <div className="date-nav__row date-nav__row--months">
        {MONTHS.map((m) => (
          <Link key={m} href={`/calendar/${year}/${m}`} className={m === month ? "is-active" : ""}>
            {monthName(m).slice(0, 3).toUpperCase()}
          </Link>
        ))}
      </div>

      {month != null && weeks.length > 0 && (
        <div className="date-nav__weeks">
          {weeks.map((week) => {
            const isSelectedWeek = week[0] === selectedWeekStart;
            return (
              <div
                key={week[0]}
                className={`date-nav__week-row${isSelectedWeek ? " is-selected" : ""}`}
              >
                <Link
                  href={`/calendar/week/${week[0]}`}
                  className={`date-nav__week-handle${isSelectedWeek ? " is-active" : ""}`}
                  aria-label={`View week of ${week[0]}`}
                >
                  W{isoWeekNumber(week[0]!)}
                </Link>
                {week.map((date) => {
                  const inMonth = date >= monthStart && date <= monthEnd;
                  return (
                    <Link
                      key={date}
                      href={`/day/${date}`}
                      className={
                        (date === selectedDate ? "is-active " : "") + (inMonth ? "" : "is-outside")
                      }
                    >
                      {parseIsoDate(date).getUTCDate()}
                    </Link>
                  );
                })}
              </div>
            );
          })}
        </div>
      )}
    </nav>
  );
}
