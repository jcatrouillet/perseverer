"""Mileage accounting for bikes and their tires.

A ride's bike is the athlete's explicit choice for that ride (`activity.bike_id`), else the dated
default for the ride's category.  Categories run from general to specific: "cycling" (every ride),
then "road", "mountain", "gravel", "ebike", "emtb" and "commute"; the most specific default that
applies wins, so a bike can be the general one and another the commuter.  As with shoes, a default
only counts rides that started at or after its own `assigned_at`: a past ride is never guessed
onto a bike.

Tires are separate records with an installation ledger (`bike_tire_install`).  A tire's distance is
its initial distance plus the distance of every ride made on a bike while the tire was installed
on it, front or rear alike, so replacing a tire, swapping it to another bike or storing it never
re-charges old rides.  Everything is computed on read from the activity summaries, never copied
into a counter, so correcting a ride's distance, bike or sport corrects the totals at once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import escape
from typing import Any

from sqlalchemy import Connection, select

from perseverer.config import Settings
from perseverer.db.schema import (
    activity,
    athlete_default_bike,
    bike,
    bike_tire,
    bike_tire_install,
)
from perseverer.email_delivery import send_email
from perseverer.merge.engine import sports_in_family

GENERAL = "cycling"
# Most specific first: the order in which a ride's own categories are tried against the defaults.
CATEGORIES: tuple[str, ...] = ("commute", "emtb", "ebike", "gravel", "mountain", "road", GENERAL)
CATEGORY_LABELS: dict[str, str] = {
    GENERAL: "All cycling",
    "road": "Road cycling",
    "mountain": "Mountain biking",
    "gravel": "Gravel bike",
    "ebike": "E-bike",
    "emtb": "eMTB",
    "commute": "Bike commute",
}
POSITIONS = ("front", "rear")


@dataclass(frozen=True)
class Ride:
    id: str
    sport: str
    sub_sport: str | None
    is_commute: bool
    bike_id: str | None
    start_time_utc: datetime
    distance_m: float


def ride_categories(ride: Ride) -> list[str]:
    """The categories a ride belongs to, most specific first, always ending with the general one.
    "commute" is the athlete's own commute tag (or a device's commuting sub-sport); the rest come
    from the sport and sub-sport the device recorded."""
    sub = ride.sub_sport or ""
    found: list[str] = []
    if ride.is_commute or sub == "commuting":
        found.append("commute")
    if sub == "e_bike_mountain":
        found.append("emtb")
    if ride.sport == "e_bike_fitness" or sub == "e_bike_fitness":
        found.append("ebike")
    if ride.sport == "gravel_cycling" or sub == "gravel_cycling":
        found.append("gravel")
    if ride.sport == "mountain_biking" or sub == "mountain":
        found.append("mountain")
    if ride.sport == "road_biking" or sub == "road":
        found.append("road")
    found.append(GENERAL)
    return found


def load_rides(conn: Connection, athlete_id: str) -> list[Ride]:
    rows = conn.execute(
        select(
            activity.c.id,
            activity.c.sport,
            activity.c.sub_sport,
            activity.c.is_commute,
            activity.c.bike_id,
            activity.c.start_time_utc,
            activity.c.distance_m,
        ).where(
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
            activity.c.sport.in_(sports_in_family("ride")),
        )
    ).fetchall()
    return [
        Ride(
            r.id,
            r.sport,
            r.sub_sport,
            bool(r.is_commute),
            r.bike_id,
            r.start_time_utc,
            float(r.distance_m or 0.0),
        )
        for r in rows
    ]


def load_defaults(conn: Connection, athlete_id: str) -> dict[str, tuple[str, datetime]]:
    """category -> (bike id, assigned_at)."""
    rows = conn.execute(
        select(
            athlete_default_bike.c.category,
            athlete_default_bike.c.bike_id,
            athlete_default_bike.c.assigned_at,
        ).where(athlete_default_bike.c.athlete_id == athlete_id)
    ).fetchall()
    return {r.category: (r.bike_id, r.assigned_at) for r in rows}


def resolve_bike(ride: Ride, defaults: dict[str, tuple[str, datetime]]) -> tuple[str | None, bool]:
    """`(bike_id, is_default)` for one ride: its explicit bike, else the most specific category
    default whose ledger had started by the ride, else no bike."""
    if ride.bike_id is not None:
        return ride.bike_id, False
    for category in ride_categories(ride):
        entry = defaults.get(category)
        if entry is not None and entry[1] <= ride.start_time_utc:
            return entry[0], True
    return None, False


@dataclass
class Ledger:
    """Everything a Gear page needs, from one pass over the rides."""

    bike_distance_m: dict[str, float] = field(default_factory=dict)  # rides only, no initial
    tire_distance_m: dict[str, float] = field(default_factory=dict)  # rides only, no initial
    default_categories: dict[str, list[str]] = field(default_factory=dict)  # bike -> categories


def compute_ledger(conn: Connection, athlete_id: str) -> Ledger:
    rides = load_rides(conn, athlete_id)
    defaults = load_defaults(conn, athlete_id)
    installs = conn.execute(
        select(
            bike_tire_install.c.tire_id,
            bike_tire_install.c.bike_id,
            bike_tire_install.c.installed_at,
            bike_tire_install.c.removed_at,
        ).where(bike_tire_install.c.athlete_id == athlete_id)
    ).fetchall()
    by_bike: dict[str, list[Any]] = {}
    for inst in installs:
        by_bike.setdefault(inst.bike_id, []).append(inst)

    ledger = Ledger()
    for category, (default_bike_id, _) in defaults.items():
        ledger.default_categories.setdefault(default_bike_id, []).append(category)
    for categories in ledger.default_categories.values():
        categories.sort(key=CATEGORIES.index)

    for ride in rides:
        bike_id, _ = resolve_bike(ride, defaults)
        if bike_id is None:
            continue
        ledger.bike_distance_m[bike_id] = ledger.bike_distance_m.get(bike_id, 0.0) + ride.distance_m
        for inst in by_bike.get(bike_id, []):
            if inst.installed_at <= ride.start_time_utc and (
                inst.removed_at is None or ride.start_time_utc < inst.removed_at
            ):
                ledger.tire_distance_m[inst.tire_id] = (
                    ledger.tire_distance_m.get(inst.tire_id, 0.0) + ride.distance_m
                )
    return ledger


@dataclass(frozen=True)
class TireAlert:
    tire: Any
    bike: Any
    position: str
    distance_m: float


def over_limit_tires(conn: Connection, athlete_id: str) -> list[TireAlert]:
    """Installed, active tires whose live distance has reached their own limit."""
    ledger = compute_ledger(conn, athlete_id)
    tires = conn.execute(
        select(bike_tire).where(
            bike_tire.c.athlete_id == athlete_id,
            bike_tire.c.retired_at.is_(None),
            bike_tire.c.max_distance_m.is_not(None),
        )
    ).fetchall()
    open_installs = {
        r.tire_id: r
        for r in conn.execute(
            select(bike_tire_install).where(
                bike_tire_install.c.athlete_id == athlete_id,
                bike_tire_install.c.removed_at.is_(None),
            )
        )
    }
    bikes = {
        r.id: r
        for r in conn.execute(
            select(bike).where(bike.c.athlete_id == athlete_id, bike.c.retired_at.is_(None))
        )
    }
    alerts: list[TireAlert] = []
    for tire in tires:
        inst = open_installs.get(tire.id)
        owner = bikes.get(inst.bike_id) if inst is not None else None
        if inst is None or owner is None:
            continue  # in stock, or on a retired bike: nothing to replace
        total = tire.initial_distance_m + ledger.tire_distance_m.get(tire.id, 0.0)
        if total >= tire.max_distance_m:
            alerts.append(TireAlert(tire, owner, inst.position, total))
    return alerts


def send_tire_alerts(
    conn: Connection, settings: Settings, athlete_id: str, recipient: str | None
) -> int:
    """Email each newly-over-limit tire once; the app banner stays live separately."""
    if not settings.smtp_configured or not recipient:
        return 0
    sent = 0
    for alert in over_limit_tires(conn, athlete_id):
        tire = alert.tire
        if tire.alert_emailed_at is not None:
            continue
        name = f"{tire.brand} {tire.model}"
        where = f"{alert.position} tire of {alert.bike.brand} {alert.bike.model}"
        used_km, max_km = alert.distance_m / 1000, tire.max_distance_m / 1000
        send_email(
            settings,
            to=recipient,
            subject=f"Perseverer: replace the {alert.position} tire ({name})",
            text_body=(
                f"{name}, the {where}, has reached {used_km:.0f} km, over its {max_km:.0f} km "
                "limit. Open Gear in Perseverer to fit a replacement."
            ),
            html_body=(
                f"<p><strong>{escape(name)}</strong>, the {escape(where)}, has reached "
                f"<strong>{used_km:.0f} km</strong>, over its {max_km:.0f} km limit.</p>"
                "<p>Open Gear in Perseverer to fit a replacement.</p>"
            ),
        )
        conn.execute(
            bike_tire.update()
            .where(bike_tire.c.id == tire.id)
            .values(alert_emailed_at=datetime.now(UTC).replace(tzinfo=None))
        )
        conn.commit()
        sent += 1
    return sent
