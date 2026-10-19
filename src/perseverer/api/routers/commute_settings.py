"""GET/PUT /settings/commute -- the athlete's optional home and work addresses, used only to spot
commutes automatically (auto_tags.py).  An address is looked up once, when it changes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Connection, select

from perseverer.api.dependencies import get_conn, require_api_key
from perseverer.api.schemas.commute import CommuteSettingsIn, CommuteSettingsOut
from perseverer.auto_tags import COMMUTE_RADIUS_M, apply_auto_tags, detected_commute_count
from perseverer.config import Settings, get_settings
from perseverer.db.schema import athlete_commute_config
from perseverer.geocoding import GeocodedAddress, GeocodingUnavailable, geocode_address

router = APIRouter()


def _out(conn: Connection, athlete_id: str) -> CommuteSettingsOut:
    row = conn.execute(
        select(athlete_commute_config).where(athlete_commute_config.c.athlete_id == athlete_id)
    ).fetchone()
    has_both = (
        row is not None
        and row.home_lat is not None
        and row.home_lon is not None
        and row.work_lat is not None
        and row.work_lon is not None
    )
    return CommuteSettingsOut(
        home_address=row.home_address if row else None,
        home_label=row.home_label if row else None,
        work_address=row.work_address if row else None,
        work_label=row.work_label if row else None,
        detection_active=bool(has_both),
        detected_count=detected_commute_count(conn, athlete_id) if has_both else 0,
        radius_m=COMMUTE_RADIUS_M,
    )


@router.get("/settings/commute")
def get_commute_settings(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> CommuteSettingsOut:
    return _out(conn, athlete_id)


def _resolve(
    conn: Connection,
    settings: Settings,
    athlete_id: str,
    typed: str | None,
    stored_address: str | None,
    stored: tuple[str | None, float | None, float | None],
    what: str,
) -> tuple[str | None, str | None, float | None, float | None]:
    """(address, label, lat, lon) for one address field: cleared, kept as is when unchanged, or
    looked up."""
    address = (typed or "").strip()
    if not address:
        return None, None, None, None
    label, lat, lon = stored
    if address == stored_address and lat is not None and lon is not None:
        return address, label, lat, lon
    try:
        found: GeocodedAddress | None = geocode_address(
            conn, settings.raw_archive_dir, athlete_id=athlete_id, query=address
        )
    except GeocodingUnavailable as e:
        raise HTTPException(
            status_code=502, detail="The address lookup service is unavailable, try again later."
        ) from e
    if found is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Couldn't find the {what} address. Try adding the city and country, or a postcode."
            ),
        )
    return address, found.label, found.lat, found.lon


@router.put("/settings/commute")
def put_commute_settings(
    payload: CommuteSettingsIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> CommuteSettingsOut:
    """Saves the addresses (an unchanged one isn't looked up again) and re-runs commute detection
    over every activity.  Nothing is saved if an address can't be found."""
    row = conn.execute(
        select(athlete_commute_config).where(athlete_commute_config.c.athlete_id == athlete_id)
    ).fetchone()
    home = _resolve(
        conn,
        settings,
        athlete_id,
        payload.home_address,
        row.home_address if row else None,
        (row.home_label, row.home_lat, row.home_lon) if row else (None, None, None),
        "home",
    )
    work = _resolve(
        conn,
        settings,
        athlete_id,
        payload.work_address,
        row.work_address if row else None,
        (row.work_label, row.work_lat, row.work_lon) if row else (None, None, None),
        "work",
    )
    values = {
        "home_address": home[0],
        "home_label": home[1],
        "home_lat": home[2],
        "home_lon": home[3],
        "work_address": work[0],
        "work_label": work[1],
        "work_lat": work[2],
        "work_lon": work[3],
        "updated_at": datetime.now(UTC).replace(tzinfo=None),
    }
    if row is None:
        conn.execute(athlete_commute_config.insert().values(athlete_id=athlete_id, **values))
    else:
        conn.execute(
            athlete_commute_config.update()
            .where(athlete_commute_config.c.athlete_id == athlete_id)
            .values(**values)
        )
    # Short transactions: the lookup is done, write the addresses, then retag.
    conn.commit()
    apply_auto_tags(conn, athlete_id=athlete_id)
    conn.commit()
    return _out(conn, athlete_id)
