"""API-level tests, focused on wiring that unit tests cannot catch."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


class TestFormRouteOrdering:
    """`/forms/builder/{x}` and `/forms/{schema}/{definition}` are both two segments.

    FastAPI resolves in declaration order, so if the generic route is registered first it
    swallows every builder request and answers "No such schema: builder". The bug is
    invisible in unit tests and shows up in the UI only as a builder form that never
    appears, so it is pinned here.
    """

    def test_builder_route_is_not_shadowed(self, client):
        response = client.get("/api/forms/builder/tmdb_trending_weekly")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["builder"] == "tmdb_trending_weekly"
        assert body["service"] == "TMDb"
        assert body["field"]["control"] == "integer"

    def test_generic_schema_route_still_resolves(self, client):
        response = client.get("/api/forms/config/settings")
        assert response.status_code == 200
        assert len(response.json()["fields"]) > 35

    def test_defaults_route_still_resolves(self, client):
        response = client.get("/api/forms/defaults/collection/oscars")
        assert response.status_code == 200
        assert response.json()["definition"] == "award-template-vars"

    def test_unknown_builder_is_404(self, client):
        assert client.get("/api/forms/builder/not_a_real_builder").status_code == 404

    def test_builder_missing_from_schema_still_gets_a_field(self, client):
        """The 20 schema-gap builders are valid in Kometa, so the UI must still offer them."""
        response = client.get("/api/forms/builder/trakt_watchlist")
        assert response.status_code == 200
        body = response.json()
        assert body["inSchema"] is False
        assert body["field"]["control"] == "yaml"


class TestWorkspaceLifecycle:
    def test_endpoints_require_an_open_workspace(self, client):
        # A fresh process has no workspace; these must fail cleanly rather than 500.
        for path in ("/api/workspace/tree", "/api/workspace/configs"):
            assert client.get(path).status_code in (200, 409)

    def test_status_is_available_without_a_workspace(self, client):
        response = client.get("/api/status")
        assert response.status_code == 200
        assert "catalog" in response.json()

    def test_schema_name_is_validated(self, client):
        """The route can only ever see one path segment.

        Traversal written literally is collapsed by the client, and percent-encoded
        slashes are decoded into extra segments that no longer match `/schemas/{name}` —
        so a traversing name cannot reach this handler at all. What is reachable, and
        therefore worth pinning, is a single segment containing dots, plus the ordinary
        unknown-name case.
        """
        assert client.get("/api/schemas/..json").status_code == 400
        assert client.get("/api/schemas/config-schema.txt").status_code == 400
        assert client.get("/api/schemas/not-a-schema.json").status_code == 404
        assert client.get("/api/schemas/config-schema.json").status_code == 200


class TestStaticServing:
    """The SPA catch-all is registered last and must not become a file-read primitive."""

    def test_never_serves_a_file_outside_the_static_directory(self, client, monkeypatch, tmp_path):
        from app import main

        if not main.STATIC_DIR.is_dir():
            pytest.skip("no built frontend to serve")

        # Anything that is not a real file under static/ falls through to index.html.
        # What matters is that no backend source or system file is ever returned.
        for path in ("/../../backend/app/config.py", "/etc/passwd", "/app/config.py"):
            response = client.get(path)
            assert "class Settings" not in response.text
            assert "root:x:" not in response.text

    def test_api_routes_are_not_shadowed_by_the_catch_all(self, client):
        assert client.get("/api/status").status_code == 200
        assert client.get("/health").json() == {"status": "ok"}


class TestDefaultsFlow:
    """The create-without-YAML path, end to end through HTTP."""

    def test_add_configure_and_remove_a_default(self, client, tmp_path):
        config = tmp_path / "config.yml"
        config.write_text(
            "# keep me\nlibraries:\n  Movies:\n    collection_files:\n      - file: movies.yml\n",
            encoding="utf-8",
        )
        (tmp_path / "movies.yml").write_text("collections: {}\n", encoding="utf-8")

        opened = client.post(
            "/api/workspace/open", json={"path": str(tmp_path), "allow_writes": True}
        )
        assert opened.status_code == 200

        added = client.post(
            "/api/defaults/add",
            json={"config": "config.yml", "library": "Movies", "kind": "collection", "name": "oscars"},
        )
        assert added.status_code == 200, added.text
        assert added.json()["changed"] is True
        assert "# keep me" in config.read_text(encoding="utf-8")

        enabled = client.get("/api/defaults/enabled", params={"config": "config.yml"}).json()["enabled"]
        assert [e["name"] for e in enabled] == ["oscars"]
        entry = enabled[0]

        configured = client.post(
            "/api/defaults/template-variables",
            json={
                "config": "config.yml",
                "library": "Movies",
                "list_key": entry["listKey"],
                "index": entry["index"],
                "template_variables": {"collection_mode": "hide_items"},
            },
        )
        assert configured.status_code == 200
        assert "collection_mode: hide_items" in config.read_text(encoding="utf-8")

        removed = client.post(
            "/api/defaults/remove",
            json={
                "config": "config.yml",
                "library": "Movies",
                "list_key": entry["listKey"],
                "index": entry["index"],
            },
        )
        assert removed.status_code == 200
        assert client.get("/api/defaults/enabled", params={"config": "config.yml"}).json()["enabled"] == []
        assert "# keep me" in config.read_text(encoding="utf-8")

    def test_edits_are_refused_on_a_locked_workspace(self, client, tmp_path):
        config = tmp_path / "config.yml"
        original = "libraries:\n  Movies:\n    collection_files: []\n"
        config.write_text(original, encoding="utf-8")

        client.post("/api/workspace/open", json={"path": str(tmp_path), "allow_writes": False})
        response = client.post(
            "/api/defaults/add",
            json={"config": "config.yml", "library": "Movies", "kind": "collection", "name": "imdb"},
        )
        assert response.status_code == 423
        assert config.read_text(encoding="utf-8") == original

    def test_creating_a_collection_validates_clean(self, client, tmp_path):
        (tmp_path / "movies.yml").write_text(
            "# hand written\ncollections:\n  Trending:\n    trakt_trending: 10\n", encoding="utf-8"
        )
        client.post("/api/workspace/open", json={"path": str(tmp_path), "allow_writes": True})

        response = client.post(
            "/api/collections/add",
            json={
                "path": "movies.yml",
                "name": "Trending This Week",
                "definition": {"tmdb_trending_weekly": 20},
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["validation"]["ok"] is True
        text = (tmp_path / "movies.yml").read_text(encoding="utf-8")
        assert "# hand written" in text
        assert "trakt_trending: 10" in text
