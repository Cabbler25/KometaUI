"""Preview translation.

The correctness that matters here is not "does it return items" but "does it refuse to
mislead". A preview that quietly drops a condition would report a larger match than the
collection will actually contain, which is worse than showing nothing.
"""

from __future__ import annotations

import pytest

from app.services import preview


class TestTranslateCondition:
    @pytest.mark.parametrize(
        ("key", "value", "expected"),
        [
            ("genre", "Action", ("genres", "Action")),
            ("year.gte", 2018, ("year__gte", 2018)),
            ("year.lte", 2020, ("year__lte", 2020)),
            ("audience_rating.gte", 7.0, ("audienceRating__gte", 7.0)),
            ("title.begins", "The", ("title__begins", "The")),
            ("content_rating", "PG-13", ("contentRating", "PG-13")),
        ],
    )
    def test_maps_kometa_syntax_to_plexapi(self, key, value, expected):
        assert preview.translate_condition(key, value) == expected

    def test_uses_kometas_own_field_vocabulary(self):
        """`unplayed` is Kometa's name; Plex calls the field `unwatched`.

        The mapping comes from the catalog, which lifts it out of modules/plex.py, so it
        tracks Kometa rather than being transcribed here.
        """
        assert preview.translate_condition("unplayed", True) == ("unwatched", True)

    def test_comma_separated_values_become_alternatives(self):
        name, value = preview.translate_condition("audio_language", "English, eng")
        assert name == "audioLanguage"
        assert value == ["English", "eng"]

    @pytest.mark.parametrize("key", ["title.regex", "title.rated"])
    def test_untranslatable_modifiers_return_none(self, key):
        """Better to report these than to drop them and inflate the count."""
        assert preview.translate_condition(key, "x") is None

    def test_a_dot_that_is_not_a_modifier_is_left_alone(self):
        result = preview.translate_condition("episode.title", "Pilot")
        assert result is not None
        assert result[0].startswith("episode")


class TestPreviewable:
    @pytest.mark.parametrize(
        ("definition", "expected"),
        [
            ({"plex_all": True}, True),
            ({"plex_search": {"all": {"genre": "Action"}}}, True),
            ({"tmdb_popular": 20}, False),
            ({"trakt_trending": 10}, False),
        ],
    )
    def test_only_plex_native_builders_qualify(self, definition, expected):
        assert preview.previewable(definition)[0] is expected

    def test_a_mixed_definition_is_not_previewable(self):
        """One remote builder makes the whole count wrong, so refuse the lot."""
        ok, blocking = preview.previewable({"plex_all": True, "trakt_trending": 10})
        assert ok is False
        assert blocking == ["trakt_trending"]

    def test_names_what_blocks_it(self):
        _, blocking = preview.previewable({"tmdb_popular": 20, "imdb_chart": "top"})
        assert set(blocking) == {"tmdb_popular", "imdb_chart"}

    def test_a_definition_with_no_builders_is_not_previewable(self):
        assert preview.previewable({"sort_title": "x"})[0] is False


class TestPreviewGuards:
    def test_requires_a_connection(self, monkeypatch):
        from app.services import connections

        monkeypatch.setattr(connections, "_session", connections.ConnectionSession())
        from app.services.plex_client import PlexError

        with pytest.raises(PlexError, match="Connect to Plex"):
            preview.preview_definition("Movies", {"plex_all": True})

    def test_remote_builders_raise_unsupported(self, monkeypatch):
        from app.services import connections

        session = connections.ConnectionSession(url="http://x", token="t")
        monkeypatch.setattr(connections, "_session", session)
        with pytest.raises(preview.PreviewUnsupported, match="external services"):
            preview.preview_definition("Movies", {"tmdb_popular": 20})


class TestSkippedReporting:
    def test_or_blocks_and_bad_modifiers_are_recorded(self):
        """These feed the UI warning that the count is incomplete."""
        result = preview.PreviewResult(total=0)
        kwargs: dict = {}
        preview._add(result, kwargs, "genre", "Action")
        preview._add(result, kwargs, "title.regex", "^The")

        assert kwargs == {"genres": "Action"}
        assert [s["condition"] for s in result.skipped] == ["title.regex"]
        assert result.applied == ["genre → genres"]
