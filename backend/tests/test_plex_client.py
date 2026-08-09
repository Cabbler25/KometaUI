"""Connection helpers.

No network: plex.tv and TMDb are stubbed. What matters here is that failures become
messages a user can act on, rather than raw library tracebacks, and that library types
come back in the vocabulary Kometa's `library_type` expects.
"""

from __future__ import annotations

import httpx
import pytest

from app.services import plex_client


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)  # type: ignore[arg-type]


class TestPinFlow:
    def test_start_pin_builds_an_auth_url_carrying_the_code(self, monkeypatch):
        monkeypatch.setattr(
            httpx, "post", lambda *a, **k: FakeResponse({"id": 42, "code": "abcd1234"})
        )
        pin = plex_client.start_pin()
        assert pin.id == 42
        assert pin.code == "abcd1234"
        assert "code=abcd1234" in pin.auth_url
        assert plex_client.CLIENT_IDENTIFIER in pin.auth_url

    def test_poll_returns_none_until_approved(self, monkeypatch):
        monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse({"authToken": None}))
        assert plex_client.poll_pin(42) is None

    def test_poll_returns_the_token_once_approved(self, monkeypatch):
        monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse({"authToken": "tok"}))
        assert plex_client.poll_pin(42) == "tok"

    def test_network_failure_is_reported_not_raised_raw(self, monkeypatch):
        def boom(*a, **k):
            raise httpx.ConnectError("no route")

        monkeypatch.setattr(httpx, "post", boom)
        with pytest.raises(plex_client.PlexError, match="Could not reach plex.tv"):
            plex_client.start_pin()


class TestServerDiscovery:
    RESOURCES = [
        {
            "name": "Tower",
            "provides": "server",
            "product": "Plex Media Server",
            "productVersion": "1.40",
            "owned": True,
            "connections": [
                {"uri": "https://relay.plex.direct", "local": False, "relay": True},
                {"uri": "http://192.168.1.10:32400", "local": True, "relay": False},
                {"uri": "https://public.plex.direct", "local": False, "relay": False},
            ],
        },
        {"name": "A Phone", "provides": "player", "connections": []},
    ]

    def test_only_servers_are_returned(self, monkeypatch):
        monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(self.RESOURCES))
        servers = plex_client.list_servers("tok")
        assert [s["name"] for s in servers] == ["Tower"]

    def test_local_connections_are_offered_before_relays(self, monkeypatch):
        """Kometa should talk to the server directly; a relay is the last resort."""
        monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(self.RESOURCES))
        connections = plex_client.list_servers("tok")[0]["connections"]
        assert connections[0]["uri"] == "http://192.168.1.10:32400"
        assert connections[-1]["relay"] is True


class TestLibraryDiscovery:
    def test_types_use_kometas_vocabulary(self, monkeypatch):
        class Section:
            def __init__(self, title, type_, key):
                self.title, self.type, self.key = title, type_, key

        class Library:
            def sections(self):
                return [Section("Movies", "movie", "1"), Section("TV", "show", "2"), Section("Tunes", "artist", "3")]

        class Server:
            library = Library()

        monkeypatch.setattr(plex_client, "_server", lambda url, token: Server())
        libraries = plex_client.discover_libraries("http://x", "tok")
        assert [(x["name"], x["libraryType"]) for x in libraries] == [
            ("Movies", "movie"),
            ("TV", "show"),
            ("Tunes", "artist"),
        ]

    def test_unknown_plex_types_pass_through(self, monkeypatch):
        """A future Plex section type should surface, not silently disappear."""
        class Section:
            title, type, key = "Photos", "photo", "9"

        class Server:
            class library:  # noqa: N801
                @staticmethod
                def sections():
                    return [Section()]

        monkeypatch.setattr(plex_client, "_server", lambda url, token: Server())
        assert plex_client.discover_libraries("http://x", "tok")[0]["libraryType"] == "photo"


class TestFriendlyErrors:
    """Raw plexapi errors are unhelpful; each common failure needs a next step."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("401 Unauthorized", "rejected the token"),
            ("Max retries exceeded", "Could not reach a Plex server"),
            ("HTTPError: 404 Not Found", "not like a Plex server"),
        ],
    )
    def test_messages_are_actionable(self, raw, expected):
        assert expected in plex_client._friendly(Exception(raw), "http://localhost:32400")

    def test_unrecognised_errors_still_surface_the_detail(self):
        assert "something odd" in plex_client._friendly(Exception("something odd"), "http://x")


class TestTmdb:
    def test_rejects_a_bad_key_with_a_clear_message(self, monkeypatch):
        monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse({}, status_code=401))
        with pytest.raises(plex_client.PlexError, match="rejected that API key"):
            plex_client.test_tmdb("nope")

    def test_accepts_a_good_key(self, monkeypatch):
        monkeypatch.setattr(
            httpx,
            "get",
            lambda *a, **k: FakeResponse({"images": {"secure_base_url": "https://image.tmdb.org/"}}),
        )
        assert plex_client.test_tmdb("good")["ok"] is True

    def test_unexpected_status_is_reported(self, monkeypatch):
        monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse({}, status_code=503))
        with pytest.raises(plex_client.PlexError, match="503"):
            plex_client.test_tmdb("key")
