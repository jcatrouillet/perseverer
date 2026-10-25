"""Year posters: a grid of every GPS trace, a heatmap around a chosen point, a calendar and a
circular view of the year's activities, as 2667x4000 PNGs on a dark ground.

Pure rendering: `load_activities` reads what to draw, the `render_*` functions turn it into a
Pillow image, and nothing here touches the network.  The API runs a render as a background job
(`api/routers/posters.py`); a render is a function of the activities it is given, so the same
selection always gives the same picture.

Colours: one per sport, races always yellow, empty calendar days grey.  Text uses Pillow's own
bundled font so no font has to be installed in the container.
"""

from __future__ import annotations

import calendar as _calendar
import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import polyline as polyline_codec
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
from sqlalchemy import Connection, and_, select

from perseverer.db.schema import (
    activity,
    athlete,
    athlete_commute_config,
    route_geom,
)

PosterKind = Literal["grid", "heatmap", "calendar", "circular"]
KINDS: tuple[PosterKind, ...] = ("grid", "heatmap", "calendar", "circular")
# Kinds drawn from GPS traces; the other two only need a date and a distance.
ROUTE_KINDS: frozenset[str] = frozenset({"grid", "heatmap"})

WIDTH, HEIGHT = 2667, 4000
MARGIN = 134
BG = (34, 34, 34)
FG = (255, 255, 255)
RACE = (255, 255, 0)
EMPTY_CELL = (68, 68, 68)
EMPTY_LETTER = (153, 153, 153)
FOOTER_TOP = 3600  # nothing but the footer below this line

# One colour per sport; any sport not listed takes the next unused colour of the spare list.
SPORT_COLORS: dict[str, tuple[int, int, int]] = {
    "running": (77, 210, 255),
    "trail_running": (77, 160, 255),
    "cycling": (255, 111, 181),
    "road_biking": (255, 111, 181),
    "mountain_biking": (255, 150, 90),
    "gravel_cycling": (255, 150, 90),
    "hiking": (123, 216, 143),
    "walking": (150, 230, 200),
    "swimming": (110, 150, 255),
    "rowing": (190, 140, 255),
    "alpine_skiing": (200, 230, 255),
}
SPARE_COLORS: tuple[tuple[int, int, int], ...] = (
    (255, 183, 77),
    (186, 104, 200),
    (129, 199, 132),
    (240, 98, 146),
    (79, 195, 247),
    (220, 231, 117),
)


@dataclass(frozen=True)
class PosterActivity:
    id: str
    local_date: date
    sport: str
    is_race: bool
    distance_m: float
    route: list[tuple[float, float]] | None  # (lat, lon) points
    start: tuple[float, float] | None


@dataclass(frozen=True)
class HeatmapView:
    center_lat: float
    center_lon: float
    radius_km: float  # half the width of the picture, as a distance on the ground
    source: str  # where the centre came from: "home_address" | "profile_home" | "activities"


class NoPosterData(Exception):
    """Nothing matched the selection, so there is nothing to draw."""


# ---------------------------------------------------------------------------- data


def load_activities(
    conn: Connection,
    athlete_id: str,
    *,
    year: int,
    sports: Sequence[str],
    include_virtual: bool,
    with_routes: bool,
) -> list[PosterActivity]:
    """The year's activities (oldest first).  With `with_routes` only those that have a GPS trace
    are returned, and their points are decoded; otherwise those that have a distance."""
    conditions = [
        activity.c.athlete_id == athlete_id,
        activity.c.deleted_at.is_(None),
        activity.c.local_date >= f"{year}-01-01",
        activity.c.local_date <= f"{year}-12-31",
    ]
    if sports:
        conditions.append(activity.c.sport.in_(list(sports)))
    if not include_virtual:
        conditions.append(activity.c.is_virtual.is_(False))
    if with_routes:
        conditions.append(route_geom.c.encoded_polyline.is_not(None))
    rows = conn.execute(
        select(
            activity.c.id,
            activity.c.local_date,
            activity.c.sport,
            activity.c.is_race,
            activity.c.distance_m,
            route_geom.c.encoded_polyline,
            route_geom.c.start_lat,
            route_geom.c.start_lng,
        )
        .select_from(activity.outerjoin(route_geom, route_geom.c.activity_id == activity.c.id))
        .where(and_(*conditions))
        .order_by(activity.c.start_time_utc)
    ).fetchall()

    out: list[PosterActivity] = []
    for r in rows:
        distance = float(r.distance_m or 0.0)
        route: list[tuple[float, float]] | None = None
        if with_routes:
            try:
                route = [(float(a), float(b)) for a, b in polyline_codec.decode(r.encoded_polyline)]
            except Exception:
                continue
            if len(route) < 2:
                continue
        elif distance <= 0:
            continue
        start = (
            (float(r.start_lat), float(r.start_lng))
            if r.start_lat is not None and r.start_lng is not None
            else None
        )
        out.append(
            PosterActivity(
                id=r.id,
                local_date=date.fromisoformat(r.local_date),
                sport=r.sport,
                is_race=bool(r.is_race),
                distance_m=distance,
                route=route,
                start=start,
            )
        )
    return out


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


def default_heatmap_view(
    conn: Connection, athlete_id: str, activities: Sequence[PosterActivity], *, share: float = 0.5
) -> HeatmapView | None:
    """The heatmap's starting point: centred on the athlete's home address (Settings -> Commute),
    else their profile home point, else the middle of where the activities start; with the
    smallest radius that holds at least `share` of the activities' starts.  None when there is no
    start point to centre on."""
    starts = [a.start for a in activities if a.start is not None]
    if not starts:
        return None
    center: tuple[float, float] | None = None
    source = "activities"
    home = conn.execute(
        select(athlete_commute_config.c.home_lat, athlete_commute_config.c.home_lon).where(
            athlete_commute_config.c.athlete_id == athlete_id
        )
    ).fetchone()
    if home is not None and home.home_lat is not None and home.home_lon is not None:
        center, source = (float(home.home_lat), float(home.home_lon)), "home_address"
    else:
        profile = conn.execute(
            select(athlete.c.home_lat, athlete.c.home_lon).where(athlete.c.id == athlete_id)
        ).fetchone()
        if profile is not None and profile.home_lat is not None and profile.home_lon is not None:
            center, source = (float(profile.home_lat), float(profile.home_lon)), "profile_home"
    if center is None:
        lats = sorted(s[0] for s in starts)
        lons = sorted(s[1] for s in starts)
        center = (lats[len(lats) // 2], lons[len(lons) // 2])
    return HeatmapView(
        center_lat=center[0],
        center_lon=center[1],
        radius_km=radius_for_share(center, starts, share),
        source=source,
    )


def radius_for_share(
    center: tuple[float, float], starts: Sequence[tuple[float, float]], share: float = 0.5
) -> float:
    """Smallest radius (km, rounded up to a tenth, at least 1) around `center` holding at least
    `share` of `starts`."""
    if not starts:
        return 1.0
    distances = sorted(_haversine_m(center[0], center[1], s[0], s[1]) for s in starts)
    needed = max(1, math.ceil(len(distances) * share))
    return max(1.0, math.ceil(distances[needed - 1] / 100) / 10)


# ---------------------------------------------------------------------------- style


def sport_colors(sports: Sequence[str]) -> dict[str, tuple[int, int, int]]:
    """A stable colour for each of `sports`: its own when it has one, else the next spare."""
    colors: dict[str, tuple[int, int, int]] = {}
    spare = iter(SPARE_COLORS * 4)
    used = {c for s, c in SPORT_COLORS.items() if s in sports}
    for sport in sorted(set(sports)):
        if sport in SPORT_COLORS:
            colors[sport] = SPORT_COLORS[sport]
        else:
            colors[sport] = next(c for c in spare if c not in used)
            used.add(colors[sport])
    return colors


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def sport_label(sport: str) -> str:
    return sport.replace("_", " ").capitalize()


def sports_label(sports: Sequence[str]) -> str:
    """The sport part of a poster's file name."""
    parts = ["".join(w.capitalize() for w in s.split("_")) for s in sorted(set(sports))]
    if not parts:
        return "All"
    return parts[0] if len(parts) == 1 else "+".join(parts) if len(parts) <= 3 else "Multisport"


def poster_filename(kind: str, year: int, sports: Sequence[str], now: datetime, token: str) -> str:
    digest = hashlib.sha256(f"{token}{kind}{year}{now.isoformat()}".encode()).hexdigest()[:4]
    return f"{sports_label(sports)}-{year}-all-{kind}-{HEIGHT}-{now:%Y%m%d}-{now:%H%M}-{digest}.png"


# ---------------------------------------------------------------------------- shared parts


@dataclass(frozen=True)
class Units:
    factor: float  # metres -> display unit
    label: str

    def fmt(self, metres: float) -> str:
        return f"{metres * self.factor:.1f}"


METRIC = Units(1 / 1000, "km")
IMPERIAL = Units(1 / 1609.344, "mi")


def _stats_lines(acts: Sequence[PosterActivity], units: Units) -> tuple[str, str] | None:
    with_distance = [a.distance_m for a in acts if a.distance_m > 0]
    if not with_distance:
        return None
    total = sum(with_distance)
    first = f"Total: {units.fmt(total)} {units.label}, {len(acts)}x"
    second = (
        f"Avg: {units.fmt(total / len(with_distance))} {units.label}",
        f"Min: {units.fmt(min(with_distance))} {units.label}",
        f"Max: {units.fmt(max(with_distance))} {units.label}",
    )
    return first, "\t".join(second)


def _footer(
    canvas: Image.Image, year: int, name: str, acts: Sequence[PosterActivity], units: Units
) -> None:
    draw = ImageDraw.Draw(canvas)
    small, big, line = _font(52), _font(128), _font(40)
    draw.text((MARGIN, 3714), "YEAR", font=small, fill=FG, anchor="lm")
    year_text = str(year)
    draw.text((MARGIN, 3826), year_text, font=big, fill=FG, anchor="lm")
    gap = int(draw.textlength(year_text, font=big)) + 70
    draw.text((MARGIN + gap, 3826), name, font=big, fill=FG, anchor="lm")
    stats = _stats_lines(acts, units)
    if stats is None:
        return
    x = 1600
    draw.text((x, 3714), "STATISTICS", font=small, fill=FG, anchor="lm")
    draw.text((x, 3784), stats[0], font=line, fill=FG, anchor="lm")
    for i, part in enumerate(stats[1].split("\t")):
        draw.text((x + (0, 306, 626)[i], 3850), part, font=line, fill=FG, anchor="lm")


def _legend(canvas: Image.Image, colors: dict[str, tuple[int, int, int]], has_race: bool) -> None:
    """Which colour is which sport; only when there is more than one thing to tell apart."""
    entries = [(sport_label(s), c) for s, c in colors.items()]
    if has_race:
        entries.append(("Race", RACE))
    if len(entries) < 2:
        return
    draw = ImageDraw.Draw(canvas)
    font = _font(38)
    x, y = MARGIN, 3636
    for label, color in entries:
        draw.rectangle((x, y - 15, x + 30, y + 15), fill=color)
        draw.text((x + 46, y), label, font=font, fill=FG, anchor="lm")
        x += 46 + int(draw.textlength(label, font=font)) + 50


def _new_canvas() -> Image.Image:
    return Image.new("RGB", (WIDTH, HEIGHT), BG)


def _color_of(a: PosterActivity, colors: dict[str, tuple[int, int, int]]) -> tuple[int, int, int]:
    return RACE if a.is_race else colors[a.sport]


# ---------------------------------------------------------------------------- projection


def _project(
    route: Sequence[tuple[float, float]], lat0: float, lon0: float
) -> list[tuple[float, float]]:
    """Latitude/longitude to metres east and north of (lat0, lon0)."""
    k = math.cos(math.radians(lat0))
    return [((lon - lon0) * 111_320.0 * k, (lat - lat0) * 110_540.0) for lat, lon in route]


# ---------------------------------------------------------------------------- grid


def _grid_layout(n: int, area_w: int, area_h: int) -> tuple[int, int, float]:
    """(columns, rows, cell size) that make the cells as large as the area allows."""
    best = (1, n, 0.0)
    for cols in range(1, n + 1):
        rows = math.ceil(n / cols)
        cell = min(area_w / cols, area_h / rows)
        if cell > best[2]:
            best = (cols, rows, cell)
    return best


def render_grid(
    acts: Sequence[PosterActivity], *, year: int, name: str, units: Units
) -> Image.Image:
    colors = sport_colors([a.sport for a in acts])
    canvas = _new_canvas()
    area = (MARGIN, 330, WIDTH - MARGIN, 3500)
    cols, _rows, cell = _grid_layout(len(acts), area[2] - area[0], area[3] - area[1])
    x0 = area[0] + ((area[2] - area[0]) - cols * cell) / 2
    ss = 3  # drawn this much larger, then reduced, so the lines are smooth
    size = max(8, int(cell))
    for i, a in enumerate(acts):
        assert a.route is not None
        col, row = i % cols, i // cols
        pts = _project(a.route, a.route[0][0], a.route[0][1])
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
        scale = size * ss * 0.74 / span
        cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        tile = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
        line = [
            (size * ss / 2 + (x - cx) * scale, size * ss / 2 - (y - cy) * scale) for x, y in pts
        ]
        ImageDraw.Draw(tile).line(
            line, fill=(*_color_of(a, colors), 255), width=5 * ss, joint="curve"
        )
        tile = tile.resize((size, size), Image.Resampling.LANCZOS)
        canvas.paste(tile, (int(x0 + col * cell), int(area[1] + row * cell)), tile)
    _legend(canvas, colors, any(a.is_race for a in acts))
    _footer(canvas, year, name, acts, units)
    return canvas


# ---------------------------------------------------------------------------- heatmap


def render_heatmap(
    acts: Sequence[PosterActivity],
    *,
    year: int,
    name: str,
    units: Units,
    view: HeatmapView,
) -> Image.Image:
    """Every trace in its real place around `view`'s centre, glowing where several overlap."""
    colors = sport_colors([a.sport for a in acts])
    canvas = _new_canvas()
    map_h = FOOTER_TOP
    half_width_m = view.radius_km * 1000 * 1.08
    scale = (WIDTH / 2) / half_width_m  # pixels per metre
    cx, cy = WIDTH / 2, map_h / 2

    halo: dict[tuple[int, int, int], Image.Image] = {}
    core: dict[tuple[int, int, int], Image.Image] = {}
    for a in acts:
        assert a.route is not None
        color = _color_of(a, colors)
        pts = _project(a.route, view.center_lat, view.center_lon)
        px = [(cx + x * scale, cy - y * scale) for x, y in pts]
        x_lo, x_hi = min(p[0] for p in px), max(p[0] for p in px)
        y_lo, y_hi = min(p[1] for p in px), max(p[1] for p in px)
        pad = 40
        if x_hi < -pad or y_hi < -pad or x_lo > WIDTH + pad or y_lo > map_h + pad:
            continue  # entirely outside the picture
        box = (
            max(0, int(x_lo) - pad),
            max(0, int(y_lo) - pad),
            min(WIDTH, int(x_hi) + pad + 1),
            min(map_h, int(y_hi) + pad + 1),
        )
        local = [(x - box[0], y - box[1]) for x, y in px]
        for store, width, value in ((halo, 36, 58), (core, 7, 255)):
            acc = store.setdefault(color, Image.new("L", (WIDTH, map_h), 0))
            layer = Image.new("L", (box[2] - box[0], box[3] - box[1]), 0)
            ImageDraw.Draw(layer).line(local, fill=value, width=width, joint="curve")
            region = acc.crop(box)
            acc.paste(ImageChops.add(region, layer), box[:2])

    for color, halo_acc in halo.items():
        glow = halo_acc.filter(ImageFilter.GaussianBlur(5))
        alpha = ImageChops.add(glow, core[color].filter(ImageFilter.GaussianBlur(0.8)))
        canvas.paste(Image.new("RGB", (WIDTH, map_h), color), (0, 0), alpha)
    _legend(canvas, colors, any(a.is_race for a in acts))
    _footer(canvas, year, name, acts, units)
    return canvas


# ---------------------------------------------------------------------------- calendar


def render_calendar(
    acts: Sequence[PosterActivity], *, year: int, name: str, units: Units
) -> Image.Image:
    colors = sport_colors([a.sport for a in acts])
    canvas = _new_canvas()
    draw = ImageDraw.Draw(canvas)
    by_day: dict[date, list[PosterActivity]] = {}
    for a in acts:
        by_day.setdefault(a.local_date, []).append(a)

    pitch = (WIDTH - 2 * MARGIN) / 37
    box = pitch - 7
    letter_font, label_font, month_font = _font(34), _font(24), _font(92)
    draw.text((MARGIN, 360), str(year), font=_font(132), fill=FG, anchor="lm")
    for month in range(1, 13):
        name_y = 528 + (month - 1) * 262
        draw.text(
            (MARGIN, name_y), _calendar.month_name[month], font=month_font, fill=FG, anchor="lm"
        )
        offset = date(year, month, 1).weekday()  # Monday = 0: weekdays line up across the months
        cell_y = name_y + 96
        for day in range(1, _calendar.monthrange(year, month)[1] + 1):
            d = date(year, month, day)
            x = MARGIN + (offset + day - 1) * pitch
            todays = by_day.get(d, [])
            if todays:
                if any(a.is_race for a in todays):
                    fill = RACE
                else:
                    best = max(todays, key=lambda a: a.distance_m)
                    fill = colors[best.sport]
                text = (0, 0, 0)
            else:
                fill, text = EMPTY_CELL, EMPTY_LETTER
            draw.rectangle((x, cell_y - box / 2, x + box, cell_y + box / 2), fill=fill)
            draw.text(
                (x + box / 2, cell_y),
                "MTWTFSS"[d.weekday()],
                font=letter_font,
                fill=text,
                anchor="mm",
            )
            total = sum(a.distance_m for a in todays)
            if total > 0:
                draw.text(
                    (x + box / 2, cell_y + box / 2 + 26),
                    units.fmt(total),
                    font=label_font,
                    fill=FG,
                    anchor="mm",
                )
    _legend(canvas, colors, any(a.is_race for a in acts))
    _footer(canvas, year, name, acts, units)
    return canvas


# ---------------------------------------------------------------------------- circular


def _arc_text(
    canvas: Image.Image,
    text: str,
    center: tuple[float, float],
    radius: float,
    theta: float,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> None:
    """`text` laid along a circle, centred on the screen angle `theta` (degrees, clockwise from
    the right), readable from outside the circle on the top half and from inside on the bottom."""
    bottom = math.sin(math.radians(theta)) > 0
    letters = list(text)
    advances = [font.getlength(ch) for ch in letters]
    total_deg = math.degrees(sum(advances) / radius)
    direction = -1 if bottom else 1
    angle = theta - direction * total_deg / 2
    for ch, adv in zip(letters, advances, strict=True):
        mid = angle + direction * math.degrees(adv / radius) / 2
        tile = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((60, 60), ch, font=font, fill=(*FG, 255), anchor="mm")
        rot = -(mid + 90) + (180 if bottom else 0)
        tile = tile.rotate(rot, resample=Image.Resampling.BICUBIC)
        x = center[0] + radius * math.cos(math.radians(mid))
        y = center[1] + radius * math.sin(math.radians(mid))
        canvas.paste(tile, (int(x - 60), int(y - 60)), tile)
        angle += direction * math.degrees(adv / radius)


def render_circular(
    acts: Sequence[PosterActivity], *, year: int, name: str, units: Units
) -> Image.Image:
    """One spoke per day around the year (January at the top, clockwise), as long as that day's
    distance; a day with several activities stacks them end to end, races in yellow."""
    colors = sport_colors([a.sport for a in acts])
    canvas = _new_canvas()
    cx, cy = WIDTH / 2, 1996.0
    inner, longest = 280.0, 830.0
    days_in_year = 366 if _calendar.isleap(year) else 365

    by_day: dict[date, list[PosterActivity]] = {}
    for a in acts:
        by_day.setdefault(a.local_date, []).append(a)
    peak = max((sum(a.distance_m for a in day) for day in by_day.values()), default=1.0) or 1.0

    ss = 2
    box = 2600
    layer = Image.new("RGB", (box * ss, box * ss), BG)
    draw = ImageDraw.Draw(layer)
    c = box * ss / 2
    for d, day in by_day.items():
        theta = math.radians(-90 + (d.timetuple().tm_yday - 0.5) / days_in_year * 360)
        r = inner
        for a in sorted(day, key=lambda a: -a.distance_m):
            length = a.distance_m / peak * longest
            x1, y1 = c + r * ss * math.cos(theta), c + r * ss * math.sin(theta)
            x2, y2 = (
                c + (r + length) * ss * math.cos(theta),
                c + (r + length) * ss * math.sin(theta),
            )
            draw.line((x1, y1, x2, y2), fill=_color_of(a, colors), width=10 * ss)
            r += length
    layer = layer.resize((box, box), Image.Resampling.LANCZOS)
    canvas.paste(layer, (int(cx - box / 2), int(cy - box / 2)))

    draw = ImageDraw.Draw(canvas)
    draw.text((cx, cy), str(year), font=_font(124), fill=FG, anchor="mm")
    month_font = _font(88)
    for month in range(1, 13):
        first = date(year, month, 1).timetuple().tm_yday - 1
        last = first + _calendar.monthrange(year, month)[1]
        theta0 = -90 + first / days_in_year * 360
        mid = -90 + (first + last) / 2 / days_in_year * 360
        a0 = math.radians(theta0)
        draw.line(
            (
                cx + 1135 * math.cos(a0),
                cy + 1135 * math.sin(a0),
                cx + 1205 * math.cos(a0),
                cy + 1205 * math.sin(a0),
            ),
            fill=FG,
            width=3,
        )
        _arc_text(canvas, _calendar.month_name[month], (cx, cy), 1160, mid, month_font)
    _legend(canvas, colors, any(a.is_race for a in acts))
    _footer(canvas, year, name, acts, units)
    return canvas


# ---------------------------------------------------------------------------- entry point


def render_poster(
    kind: PosterKind,
    acts: Sequence[PosterActivity],
    *,
    year: int,
    name: str,
    units: Units,
    view: HeatmapView | None = None,
) -> Image.Image:
    if not acts:
        raise NoPosterData("No activity matches this selection.")
    if kind == "grid":
        return render_grid(acts, year=year, name=name, units=units)
    if kind == "heatmap":
        if view is None:
            raise NoPosterData("The heatmap needs a centre point.")
        return render_heatmap(acts, year=year, name=name, units=units, view=view)
    if kind == "calendar":
        return render_calendar(acts, year=year, name=name, units=units)
    return render_circular(acts, year=year, name=name, units=units)
