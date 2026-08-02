from fastapi.testclient import TestClient

from sporthealth import __version__
from sporthealth.api.main import app


def test_healthz_ok() -> None:
    client = TestClient(app)
    response = client.get("/api/v1/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_version_reports_package_version() -> None:
    client = TestClient(app)
    response = client.get("/api/v1/version")
    assert response.status_code == 200
    assert response.json()["version"] == __version__
