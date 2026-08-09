"""Preview which items a Plex-native collection would match.

Kometa's slowest feedback loop is finding out what a collection actually contains: today
the only way is to run it and look at Plex. For builders that query Plex directly, the
same question can be answered in a second.

The translation from Kometa's filter syntax to PlexAPI's is driven by the tables lifted
out of ``modules/plex.py`` into the catalog -- ``search_translation``,
``attribute_translation``, ``modifier_translation`` -- so the vocabulary stays in step
with Kometa rather than being transcribed by hand.

**Scope is deliberately narrow.** Only ``plex_all``, ``plex_search`` and plain ``filters``
are previewable. Remote builders (``tmdb_*``, ``trakt_*``, ``imdb_*``, …) would each need
their own API credentials, rate-limit handling, and Kometa's ID-mapping cache to resolve
results back to library items; that is a much larger piece of work. Rather than quietly
previewing a subset and letting the count mislead, anything untranslatable is reported --
a preview that is silently wrong is worse than no preview.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .connections import current as current_connection
from .plex_client import PlexError, _server
from .validation import load_catalog

# Kometa modifier -> PlexAPI operator suffix. PlexAPI expresses these as `field__op`.
MODIFIER_TO_PLEXAPI = {
    "": "",
    ".is": "",
    ".not": "__ne",
    ".isnot": "__ne",
    ".gt": "__gt",
    ".gte": "__gte",
    ".lt": "__lt",
    ".lte": "__lte",
    ".after": "__gt",
    ".before": "__lt",
    ".begins": "__begins",
    ".ends": "__ends",
    ".contains": "__icontains",
}

# Builders whose results come from Plex itself and can therefore be previewed.
PREVIEWABLE_BUILDERS = {"plex_all", "plex_search"}

MAX_RESULTS = 60


@dataclass
class PreviewResult:
    total: int
    items: list[dict[str, Any]] = field(default_factory=list)
    # Conditions that were translated and applied.
    applied: list[str] = field(default_factory=list)
    # Conditions that could not be translated, with the reason. Surfaced prominently so
    # the count is never read as authoritative when something was dropped.
    skipped: list[dict[str, str]] = field(default_factory=list)
    truncated: bool = False


class PreviewUnsupported(Exception):
    """The definition uses builders that cannot be previewed from Plex alone."""


def _tables() -> tuple[dict[str, str], dict[str, str]]:
    plex = load_catalog().get("plex", {})
    return plex.get("search_translation", {}), plex.get("attribute_translation", {})


def _split_modifier(key: str) -> tuple[str, str]:
    """`year.gte` -> `('year', '.gte')`. Attributes never contain a dot themselves."""
    if "." in key:
        attribute, _, modifier = key.rpartition(".")
        candidate = f".{modifier}"
        if candidate in MODIFIER_TO_PLEXAPI or candidate in (".regex", ".rated"):
            return attribute, candidate
    return key, ""


def translate_condition(key: str, value: Any) -> tuple[str, Any] | None:
    """Turn one Kometa condition into a PlexAPI keyword argument.

    Returns ``None`` when the condition has no PlexAPI equivalent.
    """
    search_translation, attribute_translation = _tables()
    attribute, modifier = _split_modifier(key)

    if modifier in (".regex", ".rated"):
        return None

    field_name = search_translation.get(attribute, attribute_translation.get(attribute, attribute))
    # PlexAPI addresses nested fields with the same dotted form Kometa uses.
    operator = MODIFIER_TO_PLEXAPI.get(modifier)
    if operator is None:
        return None

    if isinstance(value, str) and "," in value and modifier in ("", ".is"):
        # Kometa treats a comma-separated string as a list of alternatives.
        value = [part.strip() for part in value.split(",") if part.strip()]

    return f"{field_name}{operator}", value


def previewable(definition: dict[str, Any]) -> tuple[bool, list[str]]:
    """Whether a definition can be previewed, and which builders block it."""
    catalog = load_catalog()
    all_builders = set(catalog.get("builder_groups", {}).get("all", []))
    used = [key for key in definition if key in all_builders]
    blocking = [b for b in used if b not in PREVIEWABLE_BUILDERS]
    return (bool(used) and not blocking), blocking


def preview_definition(library_name: str, definition: dict[str, Any]) -> PreviewResult:
    """Run a Plex-native definition against the live library."""
    session = current_connection()
    if not session.token or not session.url:
        raise PlexError("Connect to Plex first to preview a collection.")

    ok, blocking = previewable(definition)
    if not ok:
        if blocking:
            raise PreviewUnsupported(
                "Preview covers Plex-native builders only. "
                f"This uses {', '.join(sorted(blocking))}, which Kometa resolves through "
                "external services."
            )
        raise PreviewUnsupported("No builders to preview.")

    server = _server(session.url, session.token)
    try:
        section = server.library.section(library_name)
    except Exception as exc:
        raise PlexError(f"No library named {library_name!r} on this server.") from exc

    result = PreviewResult(total=0)
    kwargs: dict[str, Any] = {}
    limit: int | None = None
    sort: str | None = None

    search = definition.get("plex_search")
    if isinstance(search, dict):
        for key, value in search.items():
            if key == "limit":
                limit = int(value) if str(value).isdigit() else None
                continue
            if key == "sort_by":
                sort = str(value)
                continue
            if key == "validate":
                continue
            if key in ("all", "any"):
                if key == "any":
                    # PlexAPI's kwargs are ANDed; an `any` block needs its own request
                    # per condition and a union, which would change the meaning of the
                    # count. Report it rather than silently ANDing.
                    result.skipped.append(
                        {"condition": "any", "reason": "OR blocks are not previewable yet"}
                    )
                    continue
                block = value if isinstance(value, dict) else {}
                for inner_key, inner_value in block.items():
                    _add(result, kwargs, inner_key, inner_value)
                continue
            _add(result, kwargs, key, value)

    # `filters` are applied by Kometa after the builder; approximating them as search
    # arguments is close enough for a count, and anything untranslatable is reported.
    filters = definition.get("filters")
    if isinstance(filters, dict):
        for key, value in filters.items():
            _add(result, kwargs, key, value)

    if sort:
        kwargs["sort"] = sort

    try:
        items = section.search(maxresults=MAX_RESULTS + 1, **kwargs)
    except Exception as exc:
        raise PlexError(f"Plex rejected the search: {exc}") from exc

    result.truncated = len(items) > MAX_RESULTS
    shown = items[:MAX_RESULTS]
    result.total = len(items)
    result.items = [
        {
            "title": getattr(item, "title", "?"),
            "year": getattr(item, "year", None),
            "type": getattr(item, "type", None),
            "ratingKey": str(getattr(item, "ratingKey", "")),
            "thumb": getattr(item, "thumb", None),
        }
        for item in shown
    ]
    return result


def _add(result: PreviewResult, kwargs: dict[str, Any], key: str, value: Any) -> None:
    translated = translate_condition(key, value)
    if translated is None:
        result.skipped.append({"condition": key, "reason": "no PlexAPI equivalent"})
        return
    name, translated_value = translated
    kwargs[name] = translated_value
    result.applied.append(f"{key} → {name}")
