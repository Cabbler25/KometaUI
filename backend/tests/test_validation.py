"""Validation behaviour, including the deliberate schema-gap suppression."""

from __future__ import annotations

import pytest

from app.services import validation


class TestDetectKind:
    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            ({"libraries": {}}, "config"),
            ({"plex": {"url": "x"}}, "config"),
            ({"collections": {}}, "collection"),
            ({"dynamic_collections": {}}, "collection"),
            ({"overlays": {}}, "overlay"),
            ({"playlists": {}}, "playlist"),
            ({"metadata": {}}, "metadata"),
            ({"templates": {}}, "template"),
            ({"unrelated": 1}, None),
            ("not a mapping", None),
        ],
    )
    def test_infers_file_type(self, data, expected):
        assert validation.detect_kind(data) == expected

    def test_config_wins_over_definition_keys(self):
        """A config that also mentions collections is still a config."""
        assert validation.detect_kind({"libraries": {}, "collections": {}}) == "config"


class TestValidateText:
    def test_reports_syntax_errors_with_a_line_number(self):
        result = validation.validate_text("libraries:\n  - [unclosed\n", "c.yml")
        assert not result.ok
        assert result.engine == "syntax"
        assert result.findings[0].line is not None

    def test_accepts_a_minimal_valid_config(self):
        text = "plex:\n  url: http://localhost:32400\n  token: abc\ntmdb:\n  apikey: xyz\nlibraries:\n  Movies: {}\n"
        assert validation.validate_text(text, "config.yml").ok

    def test_flags_a_wrongly_typed_setting(self):
        text = (
            "plex:\n  url: http://localhost:32400\n  token: abc\n"
            "tmdb:\n  apikey: xyz\nlibraries:\n  Movies: {}\n"
            "settings:\n  cache: maybe\n"
        )
        result = validation.validate_text(text, "config.yml")
        assert not result.ok
        assert any("cache" in f.path or "cache" in f.message for f in result.findings)

    def test_empty_file_is_a_warning_not_an_error(self):
        result = validation.validate_text("", "c.yml")
        assert result.ok
        assert result.findings[0].severity == "warning"

    def test_unrecognisable_file_is_a_warning_not_an_error(self):
        result = validation.validate_text("something: else\n", "c.yml")
        assert result.ok
        assert result.findings[0].severity == "warning"


class TestSchemaGapSuppression:
    """Kometa's schema omits 20 builders that Kometa itself accepts.

    ``json-schema/README.md`` documents the schemas as incomplete, and the catalog
    generator measures the gap. Flagging these would be a false positive that trains users
    to ignore the validator.
    """

    def test_gap_list_is_populated(self):
        gaps = validation.schema_gap_builders()
        assert "trakt_watchlist" in gaps
        assert "mal_genre" in gaps

    def test_a_builder_missing_from_the_schema_is_accepted(self):
        text = "collections:\n  My List:\n    trakt_watchlist: me\n"
        result = validation.validate_text(text, "movies.yml")
        assert result.ok, [f.message for f in result.findings]

    def test_a_genuinely_unknown_key_is_still_rejected(self):
        """Suppression must be narrow: only the measured gap, not everything."""
        text = "collections:\n  My List:\n    definitely_not_a_builder: 1\n"
        result = validation.validate_text(text, "movies.yml")
        assert not result.ok


class TestEngineStatus:
    def test_reports_unavailable_without_a_source_path(self, monkeypatch):
        validation.kometa_engine_status.cache_clear()
        monkeypatch.setattr(validation.settings, "kometa_source_path", None)
        status = validation.kometa_engine_status()
        assert status.kometa_available is False
        assert status.detail
        validation.kometa_engine_status.cache_clear()

    def test_names_the_missing_dependency_when_import_fails(self, monkeypatch, tmp_path):
        """An unhelpful 'unavailable' is a dead end; the user needs to know what to install."""
        validation.kometa_engine_status.cache_clear()
        fake = tmp_path / "kometa"
        (fake / "modules").mkdir(parents=True)
        (fake / "modules" / "validator.py").write_text("import definitely_missing_pkg\n", encoding="utf-8")
        monkeypatch.setattr(validation.settings, "kometa_source_path", fake)
        status = validation.kometa_engine_status()
        assert status.kometa_available is False
        assert "definitely_missing_pkg" in (status.detail or "")
        validation.kometa_engine_status.cache_clear()


class TestCatalog:
    def test_catalog_is_present_and_consistent(self):
        catalog = validation.load_catalog()
        assert catalog, "run tools/generate_catalog.py"
        diagnostics = catalog["diagnostics"]
        assert diagnostics["unresolved_constants"] == []
        assert diagnostics["builders_missing_from_services"] == []
        assert diagnostics["builders_not_in_all_builders"] == []
        assert diagnostics["all_builders_count"] > 100

    def test_every_available_default_maps_to_a_template_variable_definition(self):
        """The Defaults browser generates its forms from these references, so each
        offered default must resolve to a definition that exists in the schema."""
        catalog = validation.load_catalog()
        schema = validation.load_schema("config-schema.json")
        assert schema is not None
        definitions = schema["definitions"]
        for group in ("collections", "overlays", "playlists"):
            entry = catalog["defaults"][group]
            shared = entry["shared_template_variable_ref"]
            assert entry["names"], f"{group} has no available defaults"
            for name in entry["names"]:
                ref = entry["template_variable_refs"].get(name) or shared
                assert ref, f"{group}/{name} has no template variable definition"
                assert ref in definitions, f"{group}/{name} points at unknown definition {ref}"

    def test_defaults_offered_are_ones_kometa_actually_ships(self):
        """Kometa 2.4.6's schema still lists ``flixpatrol``, but the file was removed.

        Offering it would produce a config Kometa fails to load, so the catalog filters
        the enum against the files on disk and records what it dropped.
        """
        catalog = validation.load_catalog()
        collections = catalog["defaults"]["collections"]
        assert "flixpatrol" not in collections["names"]
        assert "flixpatrol" in collections["declared_but_missing"]
        # Every offered default must name a real file.
        assert set(collections["names"]) == set(collections["files"])
