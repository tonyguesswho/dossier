"""Integration tests for dossier.api.routes.investigations.

Uses FastAPI TestClient + DOSSIER_AUTH_DEV_BYPASS to skip real Clerk auth.
Monkeypatches _dispatch_pipeline to a no-op — pipeline orchestration is tested
in tests/integration/test_pipeline_smoke.py.

Locked by 02-CONTEXT.md D-14, D-18, D-19, D-23, D-24, D-27.
Skipped when DATABASE_URL is not set.
Target runtime: <5s.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from dossier.api import main as main_module
from dossier.api.routes import investigations as routes_module
from dossier.core import db as db_module


pytestmark = pytest.mark.integration


def _require_db() -> None:
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set")


TEST_USER = "clerk-user-routes-test"


@pytest.fixture(autouse=True)
def _dev_bypass(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DOSSIER_AUTH_DEV_BYPASS", TEST_USER)
    # CLERK_JWKS_URL must be unset so the dev-bypass takes over; not required by Plan 02-03
    # dependency shape but belt-and-suspenders.
    monkeypatch.delenv("CLERK_JWKS_URL", raising=False)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Replace _dispatch_pipeline with a no-op so BackgroundTasks don't run run_investigation.
    monkeypatch.setattr(routes_module, "_dispatch_pipeline", lambda bg, inv_id: None)
    return TestClient(main_module.app)


@pytest.fixture(autouse=True)
def _cleanup():
    _require_db()
    db_module._reset_engine_for_tests()
    engine = db_module.get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM investigations WHERE user_id = :u"), {"u": TEST_USER})
        conn.execute(text("DELETE FROM investigations WHERE user_id = :u"), {"u": "other-user"})
        conn.execute(text("DELETE FROM users WHERE id IN (:u, :o)"), {"u": TEST_USER, "o": "other-user"})
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM investigations WHERE user_id = :u"), {"u": TEST_USER})
        conn.execute(text("DELETE FROM investigations WHERE user_id = :u"), {"u": "other-user"})
        conn.execute(text("DELETE FROM users WHERE id IN (:u, :o)"), {"u": TEST_USER, "o": "other-user"})


def test_post_investigations_name_returns_202(client: TestClient) -> None:
    r = client.post("/investigations", json={"kind": "name", "value": "Acme AI"})
    assert r.status_code == 202
    body = r.json()
    assert "id" in body and body["status"] == "queued"


def test_post_investigations_url_auto_https(client: TestClient) -> None:
    r = client.post("/investigations", json={"kind": "url", "value": "acme.ai"})
    assert r.status_code == 202


def test_post_rejects_ftp_url_422(client: TestClient) -> None:
    r = client.post("/investigations", json={"kind": "url", "value": "ftp://acme.ai"})
    assert r.status_code == 422
    assert "guardrail_rejected" in r.text


def test_post_rejects_injection_substring_422(client: TestClient) -> None:
    r = client.post("/investigations", json={"kind": "name", "value": "Acme <script>"})
    assert r.status_code == 422


def test_post_rate_limits_after_10(client: TestClient) -> None:
    for _ in range(10):
        assert client.post("/investigations", json={"kind": "name", "value": "Acme"}).status_code == 202
    r = client.post("/investigations", json={"kind": "name", "value": "Acme"})
    assert r.status_code == 429
    assert "rate_limited" in r.text


def test_get_list_returns_user_rows_newest_first(client: TestClient) -> None:
    client.post("/investigations", json={"kind": "name", "value": "Acme"})
    client.post("/investigations", json={"kind": "name", "value": "Beta"})
    r = client.get("/investigations")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 2
    names = [i["display_name"] for i in items]
    # Newest first — Beta created second so should come first
    assert names[0] == "Beta"


def test_get_status_returns_counts(client: TestClient) -> None:
    created = client.post("/investigations", json={"kind": "name", "value": "Acme"}).json()
    r = client.get(f"/investigations/{created['id']}/status")
    assert r.status_code == 200
    body = r.json()
    assert body["sources_count"] == 0
    assert body["claims_count"] == 0
    assert body["status"] == "queued"


def test_get_brief_409_when_not_complete(client: TestClient) -> None:
    created = client.post("/investigations", json={"kind": "name", "value": "Acme"}).json()
    r = client.get(f"/investigations/{created['id']}/brief")
    assert r.status_code == 409


def test_patch_rename_preserves_hint(client: TestClient) -> None:
    created = client.post(
        "/investigations",
        json={"kind": "name", "value": "Acme", "context_hint": "Series A meeting"},
    ).json()
    r = client.patch(f"/investigations/{created['id']}", json={"display_name": "Acme AI v2"})
    assert r.status_code == 200
    assert r.json()["display_name"] == "Acme AI v2"

    # Read from DB and verify hint is still attached via HINT_SEPARATOR
    engine = db_module.get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT input_ref FROM investigations WHERE id = :id"),
            {"id": created["id"]},
        ).fetchone()
    assert "Acme AI v2" in row.input_ref
    assert "Series A meeting" in row.input_ref


def test_delete_removes_investigation(client: TestClient) -> None:
    created = client.post("/investigations", json={"kind": "name", "value": "Acme"}).json()
    r = client.delete(f"/investigations/{created['id']}")
    assert r.status_code == 204
    # Follow-up GET 404
    assert client.get(f"/investigations/{created['id']}/status").status_code == 404


def test_rerun_creates_new_row_with_re_run_of(client: TestClient) -> None:
    original = client.post("/investigations", json={"kind": "name", "value": "Acme"}).json()
    r = client.post(f"/investigations/{original['id']}/re-run")
    assert r.status_code == 202
    new_id = r.json()["id"]
    assert new_id != original["id"]

    engine = db_module.get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT re_run_of FROM investigations WHERE id = :id"),
            {"id": new_id},
        ).fetchone()
    assert str(row.re_run_of) == original["id"]


def test_cross_user_access_returns_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Create row as TEST_USER
    created = client.post("/investigations", json={"kind": "name", "value": "Acme"}).json()

    # Switch the dev-bypass to a different user; same row should now be 404.
    monkeypatch.setenv("DOSSIER_AUTH_DEV_BYPASS", "other-user")
    client2 = TestClient(main_module.app)
    r = client2.get(f"/investigations/{created['id']}/status")
    assert r.status_code == 404  # D-24: not 403 — the id is simply "not found" for this user.
