"""Read-only Plex and TMDb access.

Two jobs, both in service of getting a config right before Kometa ever runs:

* **Getting a token without a scavenger hunt.** Plex's own advice is to view an item's XML
  and copy ``X-Plex-Token`` out of the URL. The PIN flow here does the same thing properly
  -- the user approves a short code on plex.tv and the token comes back over the API.
* **Discovering libraries.** Library names in ``config.yml`` must match the server exactly,
  and a typo produces a confusing no-op rather than an error. Reading the real names, and
  their types, removes the guesswork and lets the collection builder filter to the
  builders that can actually apply.

Everything here is strictly read-only: it lists libraries and reports versions. Nothing in
KometaUI writes to Plex -- only Kometa does that.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import httpx

PLEX_TV = "https://plex.tv/api/v2"
PLEX_AUTH_URL = "https://app.plex.tv/auth#!"
TMDB_API = "https://api.themoviedb.org/3"

PRODUCT = "KometaUI"

# Identifies this installation to plex.tv. Generated once per process: it only needs to be
# stable for the lifetime of a sign-in attempt, and not persisting it means we never leave
# an identifier behind on disk.
CLIENT_IDENTIFIER = str(uuid.uuid4())

TIMEOUT = httpx.Timeout(10.0, connect=5.0)


def _plex_headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "X-Plex-Product": PRODUCT,
        "X-Plex-Version": "0.1.0",
        "X-Plex-Client-Identifier": CLIENT_IDENTIFIER,
        "X-Plex-Platform": "Web",
    }


class PlexError(Exception):
    """A Plex or TMDb interaction failed in a way worth showing the user."""


# ----------------------------------------------------------------------------------
# PIN sign-in
# ----------------------------------------------------------------------------------


@dataclass
class PinRequest:
    id: int
    code: str
    auth_url: str


def start_pin() -> PinRequest:
    """Ask plex.tv for a linking code."""
    try:
        response = httpx.post(
            f"{PLEX_TV}/pins",
            params={"strong": "true"},
            headers=_plex_headers(),
            timeout=TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise PlexError(f"Could not reach plex.tv: {exc}") from exc

    body = response.json()
    auth_url = (
        f"{PLEX_AUTH_URL}?clientID={CLIENT_IDENTIFIER}"
        f"&code={body['code']}"
        f"&context%5Bdevice%5D%5Bproduct%5D={PRODUCT}"
    )
    return PinRequest(id=body["id"], code=body["code"], auth_url=auth_url)


def poll_pin(pin_id: int) -> str | None:
    """Return the auth token once the user has approved the code, else ``None``."""
    try:
        response = httpx.get(
            f"{PLEX_TV}/pins/{pin_id}",
            headers=_plex_headers(),
            timeout=TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise PlexError(f"Could not check the sign-in code: {exc}") from exc

    return response.json().get("authToken") or None


def list_servers(token: str) -> list[dict[str, Any]]:
    """Plex servers this account can reach, with their connection URLs.

    Saves the user having to know their server's local address. Local (non-relayed)
    connections are listed first because they are the ones Kometa should use.
    """
    try:
        response = httpx.get(
            f"{PLEX_TV}/resources",
            params={"includeHttps": "1", "includeRelay": "1"},
            headers={**_plex_headers(), "X-Plex-Token": token},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise PlexError(f"Could not list your Plex servers: {exc}") from exc

    servers = []
    for resource in response.json():
        if "server" not in (resource.get("provides") or ""):
            continue
        connections = [
            {"uri": c["uri"], "local": bool(c.get("local")), "relay": bool(c.get("relay"))}
            for c in resource.get("connections", [])
        ]
        connections.sort(key=lambda c: (c["relay"], not c["local"]))
        servers.append(
            {
                "name": resource.get("name"),
                "product": resource.get("product"),
                "version": resource.get("productVersion"),
                "owned": bool(resource.get("owned")),
                "connections": connections,
            }
        )
    return servers


# ----------------------------------------------------------------------------------
# Server inspection
# ----------------------------------------------------------------------------------


@dataclass
class ServerInfo:
    name: str
    version: str
    platform: str


# Kometa's library_type vocabulary, keyed by the Plex section type it corresponds to.
SECTION_TYPE_TO_KOMETA = {
    "movie": "movie",
    "show": "show",
    "artist": "artist",
}


def _server(url: str, token: str):
    from plexapi.server import PlexServer

    try:
        return PlexServer(url.rstrip("/"), token, timeout=10)
    except Exception as exc:  # plexapi raises a wide range of types
        raise PlexError(_friendly(exc, url)) from exc


def _friendly(exc: Exception, url: str) -> str:
    """Turn plexapi's noisy failures into something a user can act on."""
    text = str(exc)
    if "401" in text or "Unauthorized" in text:
        return "Plex rejected the token. Sign in again or paste a fresh token."
    if "Connection" in text or "timed out" in text.lower() or "Max retries" in text:
        return f"Could not reach a Plex server at {url}. Check the address and that the server is running."
    if "404" in text:
        return f"{url} responded, but not like a Plex server. Check the port (usually 32400)."
    return f"Plex connection failed: {text}"


def test_connection(url: str, token: str) -> ServerInfo:
    """Confirm a URL and token reach a Plex server."""
    server = _server(url, token)
    return ServerInfo(
        name=server.friendlyName,
        version=server.version,
        platform=f"{server.platform} {server.platformVersion}".strip(),
    )


def discover_libraries(url: str, token: str) -> list[dict[str, Any]]:
    """List the server's libraries with the type vocabulary Kometa expects."""
    server = _server(url, token)
    libraries = []
    for section in server.library.sections():
        libraries.append(
            {
                "name": section.title,
                "plexType": section.type,
                # `library_type` in config.yml; unknown Plex types are passed through so a
                # future Plex addition shows up rather than silently vanishing.
                "libraryType": SECTION_TYPE_TO_KOMETA.get(section.type, section.type),
                "key": section.key,
            }
        )
    return libraries


# ----------------------------------------------------------------------------------
# TMDb
# ----------------------------------------------------------------------------------


def test_tmdb(apikey: str) -> dict[str, Any]:
    """Check a TMDb v3 API key. Kometa refuses to start without a working one."""
    try:
        response = httpx.get(f"{TMDB_API}/configuration", params={"api_key": apikey}, timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        raise PlexError(f"Could not reach TMDb: {exc}") from exc

    if response.status_code == 401:
        raise PlexError("TMDb rejected that API key.")
    if response.status_code != 200:
        raise PlexError(f"TMDb returned {response.status_code}.")

    images = response.json().get("images", {})
    return {"ok": True, "imageBase": images.get("secure_base_url")}
