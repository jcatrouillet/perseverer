"""Garmin Personal API (read-only key) tests against a mock HTTP transport; no real network.

Properties that matter: the key is exchanged for a bearer token and never sent anywhere else, a
429 aborts the run without retry, the sync ingests through the same archive/parsers as a login
does, and what the key cannot serve (readiness, training status, predictions, lactate threshold,
sets, writes) is skipped or refused instead of failing the run.
"""

import datetime as dt
import io
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectTooManyRequestsError,
)
from sqlalchemy import select

from perseverer.adapters.garmin_connect import (
    GarminAuthRequired,
    GarminConnectAdapter,
    GarminRateLimitAborted,
    RateLimiter,
    RateLimitSettings,
    sync_garmin_connect,
    token_store_status,
)
from perseverer.adapters.garmin_personal_api import (
    TOKEN_PATH,
    PersonalApiClient,
    PersonalApiReadOnly,
    api_key_age_days,
    connect_with_api_key,
    delete_api_key,
    has_api_key,
    load_api_key,
    save_api_key,
)
from perseverer.db.engine import make_engine
from perseverer.db.schema import athlete, metadata, raw_object
from perseverer.db.seed import DEFAULT_ATHLETE_ID

FIXTURE = Path(__file__).parent.parent / "fixtures" / "fit" / "synthetic_run.fit"
JWT = "aaa.bbb.ccc"
KEY = "my-secret-key"


class Garmin:
    """A mock Personal API: records every request, answers the endpoints the sync uses."""

    def __init__(self, *, activities: list[dict[str, Any]] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.activities = activities if activities is not None else []
        self.token_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == TOKEN_PATH:
            assert request.headers["Authorization"] == f"ApiKey {KEY}"
            return httpx.Response(self.token_status, text=JWT if self.token_status == 200 else "")
        assert request.headers["Authorization"] == f"Bearer {JWT}"
        if path == "/activitylist-service/activities/search/activities":
            start = int(request.url.params["start"])
            limit = int(request.url.params["limit"])
            return httpx.Response(200, json=self.activities[start : start + limit])
        if path.startswith("/download-service/files/activity/"):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as zf:
                zf.writestr("activity.fit", FIXTURE.read_bytes())
            return httpx.Response(200, content=buffer.getvalue())
        if path == "/usersummary-service/usersummary/daily":
            return httpx.Response(200, json={"calendarDate": request.url.params["calendarDate"]})
        if path == "/sleep-service/sleep/dailySleep":
            return httpx.Response(200, json={"calendarDate": request.url.params["date"]})
        if path.startswith("/hrv-service/hrv/"):
            return httpx.Response(204)
        if path.startswith("/usersummary-service/stats/hydration/daily/"):
            day = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json=[{"calendarDate": day, "valueInML": 750}])
        if path == "/wellness-service/wellness/bodyBattery/reports/daily":
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


def _client(garmin: Garmin) -> PersonalApiClient:
    return PersonalApiClient(KEY, base_url="https://garmin.test", http=garmin.client())


def test_the_key_is_exchanged_for_a_bearer_token_and_never_sent_again() -> None:
    garmin = Garmin()
    client = _client(garmin)
    client.authenticate()
    client.get_stats("2026-01-02")
    assert garmin.paths() == [TOKEN_PATH, "/usersummary-service/usersummary/daily"]
    assert all(KEY not in r.headers.get("Authorization", "")[7:] for r in garmin.requests[1:])


@pytest.mark.parametrize("status", [400, 401, 403])
def test_a_rejected_key_is_an_authentication_error(status: int) -> None:
    garmin = Garmin()
    garmin.token_status = status
    with pytest.raises(GarminConnectAuthenticationError):
        _client(garmin).authenticate()


def test_a_429_from_the_token_endpoint_is_the_rate_limit_error() -> None:
    garmin = Garmin()
    garmin.token_status = 429
    with pytest.raises(GarminConnectTooManyRequestsError):
        _client(garmin).authenticate()


def test_activities_are_paged_until_a_short_page() -> None:
    activities = [{"activityId": i} for i in range(150)]
    garmin = Garmin(activities=activities)
    waits: list[int] = []
    client = PersonalApiClient(
        KEY,
        base_url="https://garmin.test",
        http=garmin.client(),
        throttle=lambda: waits.append(1),
    )
    found = client.get_activities_by_date("2026-01-01", "2026-01-31")
    assert [a["activityId"] for a in found] == list(range(150))
    assert len(waits) == 1  # one extra request, after the first page, goes through the throttle


def test_sleep_is_wrapped_like_the_login_api_and_hrv_204_is_none() -> None:
    client = _client(Garmin())
    assert client.get_sleep_data("2026-01-02") == {"dailySleepDTO": {"calendarDate": "2026-01-02"}}
    assert client.get_hrv_data("2026-01-02") is None
    assert client.get_hydration_data("2026-01-02") == {
        "calendarDate": "2026-01-02",
        "valueInML": 750,
    }


def test_key_storage_roundtrip_and_removal(tmp_path: Path) -> None:
    store = tmp_path / "tokens"
    assert not has_api_key(store)
    save_api_key(store, f"  {KEY}  ", base_url="https://garmin.test/")
    assert load_api_key(store) == (KEY, "https://garmin.test")
    assert has_api_key(store)
    assert token_store_status(store)[0]
    assert api_key_age_days(store) == 0
    assert delete_api_key(store) is True
    assert delete_api_key(store) is False
    with pytest.raises(GarminConnectAuthenticationError):
        load_api_key(store)


def test_a_bad_key_is_not_stored(tmp_path: Path) -> None:
    garmin = Garmin()
    garmin.token_status = 401
    with pytest.raises(GarminConnectAuthenticationError):
        connect_with_api_key(tmp_path, KEY, base_url="https://garmin.test", http=garmin.client())
    assert not has_api_key(tmp_path)
    with pytest.raises(GarminConnectAuthenticationError):
        connect_with_api_key(tmp_path, "  ", http=garmin.client())


def test_adapter_uses_the_key_without_any_login_client(tmp_path: Path) -> None:
    garmin = Garmin()
    store = tmp_path / "tokens"
    save_api_key(store, KEY, base_url="https://garmin.test")

    def no_login() -> Any:
        raise AssertionError("a key-connected adapter must never build a login client")

    adapter = GarminConnectAdapter(
        store, RateLimiter(0, 999), client_factory=no_login, http_client=garmin.client()
    )
    adapter.authenticate()
    assert adapter.personal_api
    assert not adapter.supports("training_readiness")
    assert adapter.supports("anything-else")


def test_a_revoked_key_asks_for_a_new_one(tmp_path: Path) -> None:
    garmin = Garmin()
    garmin.token_status = 401
    store = tmp_path / "tokens"
    save_api_key(store, KEY, base_url="https://garmin.test")
    adapter = GarminConnectAdapter(store, RateLimiter(0, 999), http_client=garmin.client())
    with pytest.raises(GarminAuthRequired, match="personal API key"):
        adapter.authenticate()


def test_a_429_while_syncing_aborts_without_retry(tmp_path: Path) -> None:
    garmin = Garmin(activities=[{"activityId": 1}])
    store = tmp_path / "tokens"
    save_api_key(store, KEY, base_url="https://garmin.test")

    def handler(request: httpx.Request) -> httpx.Response:
        garmin.requests.append(request)
        if request.url.path == TOKEN_PATH:
            return httpx.Response(200, text=JWT)
        return httpx.Response(429)

    adapter = GarminConnectAdapter(
        store,
        RateLimiter(0, 999),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    adapter.authenticate()
    with pytest.raises(GarminRateLimitAborted):
        adapter.list_activity_summaries(dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    assert garmin.paths().count("/activitylist-service/activities/search/activities") == 1


def test_writes_are_refused_for_a_key(tmp_path: Path) -> None:
    garmin = Garmin()
    store = tmp_path / "tokens"
    save_api_key(store, KEY, base_url="https://garmin.test")
    adapter = GarminConnectAdapter(store, RateLimiter(0, 999), http_client=garmin.client())
    adapter.authenticate()
    with pytest.raises(PersonalApiReadOnly):
        adapter.delete_workout(1)
    with pytest.raises(PersonalApiReadOnly):
        adapter.delete_course(1)


def test_a_full_sync_with_a_key_ingests_and_skips_what_the_key_cannot_serve(
    tmp_path: Path,
) -> None:
    garmin = Garmin(activities=[{"activityId": 42, "activityType": {"typeKey": "running"}}])
    store = tmp_path / "tokens"
    save_api_key(store, KEY, base_url="https://garmin.test")
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=DEFAULT_ATHLETE_ID,
                display_name="Test",
                timezone="UTC",
                unit_preference="metric",
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        conn.commit()
        summary = sync_garmin_connect(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            store,
            athlete_id=DEFAULT_ATHLETE_ID,
            rolling_window_days=2,
            rate_limits=RateLimitSettings(request_interval_s=0, max_requests_per_hour=9999),
            http_client=garmin.client(),
        )
        kinds = set(
            conn.execute(
                select(raw_object.c.kind).where(raw_object.c.source == "garmin_connect")
            ).scalars()
        )

    assert summary.errors == []
    assert summary.items_new == 1
    assert {
        "fit_activity",
        "garmin_connect_json",
        "garmin_connect_daily_summary_json",
        "garmin_connect_daily_sleep_json",
        "garmin_connect_daily_hrv_json",
        "garmin_connect_daily_hydration_json",
        "garmin_connect_daily_body_battery_json",
    } <= kinds
    assert not kinds & {
        "garmin_connect_daily_training_readiness_json",
        "garmin_connect_daily_training_status_json",
        "garmin_connect_race_predictions_json",
        "garmin_connect_lactate_threshold_json",
        "garmin_connect_daily_stress_json",
    }
    # One token exchange for the whole run, and only documented Personal API paths were called.
    assert garmin.paths().count(TOKEN_PATH) == 1
    assert not any("trainingreadiness" in p.lower() or "metrics" in p for p in garmin.paths())
