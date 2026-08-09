"""Server-side connection session.

The Plex token lives here rather than in the browser, for two reasons.

*It survives a page reload.* Holding it in React state meant refreshing the page logged
you out and lost the discovered libraries, even though nothing had actually expired.

*It never reaches the browser.* Sign-in used to hand the token back to the frontend so it
could be attached to later calls. Now the backend keeps it and attaches it itself, so the
token exists in exactly one place. The API only ever reports whether a token is held.

State is deliberately in-memory. A restart clears it, and the session is re-seeded from the
open ``config.yml`` -- which is where a token the user chose to save already lives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .yaml_doc import loads
from ruamel.yaml.error import YAMLError


@dataclass
class ConnectionSession:
    """Everything known about the current Plex/TMDb connections."""

    url: str | None = None
    # Never serialised to a client. See `public()`.
    token: str | None = None
    apikey: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    libraries: list[dict[str, Any]] = field(default_factory=list)
    plex_error: str | None = None
    tmdb_ok: bool | None = None
    tmdb_error: str | None = None
    # True when the token came from the config rather than an interactive sign-in; the UI
    # uses this to explain why it did not have to prompt.
    token_from_config: bool = False

    def public(self) -> dict[str, Any]:
        """The client-safe view. The token is reported only as present or absent."""
        return {
            "url": self.url,
            "hasToken": bool(self.token),
            "tokenFromConfig": self.token_from_config,
            "hasApikey": bool(self.apikey),
            "serverName": self.server_name,
            "serverVersion": self.server_version,
            "libraries": self.libraries,
            "plexError": self.plex_error,
            "tmdbOk": self.tmdb_ok,
            "tmdbError": self.tmdb_error,
        }


_session = ConnectionSession()


def current() -> ConnectionSession:
    return _session


def reset() -> ConnectionSession:
    global _session
    _session = ConnectionSession()
    return _session


def set_token(token: str, from_config: bool = False) -> None:
    _session.token = token or None
    _session.token_from_config = from_config
    # A new token invalidates whatever the old one told us.
    _session.server_name = None
    _session.server_version = None
    _session.libraries = []
    _session.plex_error = None


def seed_from_config(config_text: str) -> ConnectionSession:
    """Adopt the connection details already present in a config file.

    Someone who has run Kometa before already has a working token in ``config.yml``.
    Making them sign in again to see their libraries would be busywork, so the session
    starts from whatever the config provides. Values the user has already established
    interactively win, since those are the more recently confirmed ones.
    """
    try:
        data = loads(config_text)
    except YAMLError:
        return _session
    if not isinstance(data, dict):
        return _session

    plex = data.get("plex") if isinstance(data.get("plex"), dict) else {}
    tmdb = data.get("tmdb") if isinstance(data.get("tmdb"), dict) else {}

    url = plex.get("url")
    token = plex.get("token")
    apikey = tmdb.get("apikey")

    if not _session.url and isinstance(url, str) and url.strip():
        _session.url = url.strip()
    if not _session.token and isinstance(token, str) and token.strip():
        _session.token = token.strip()
        _session.token_from_config = True
    if not _session.apikey and isinstance(apikey, str) and apikey.strip():
        _session.apikey = apikey.strip()

    return _session
