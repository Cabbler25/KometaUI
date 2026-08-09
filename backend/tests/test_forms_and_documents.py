"""Form derivation and the structured document operations behind the create flows."""

from __future__ import annotations

import pytest

from app.services import documents, schema_form as sf, validation
from app.services.yaml_doc import loads
from app.services.yaml_edit import EditError

CONFIG = """\
# My Kometa config — comments must survive every form edit.
libraries:
  Movies:                    # main film library
    collection_files:
      - file: config/movies.yml
  Shows: {}
settings:
  cache: true
"""


@pytest.fixture(scope="module")
def config_schema():
    schema = validation.load_schema("config-schema.json")
    assert schema is not None, "run tools/generate_catalog.py"
    return schema


class TestFormModel:
    def test_settings_is_fully_renderable(self, config_schema):
        """Every global setting must map to a real widget, not a YAML escape hatch."""
        fields = sf.form_model(config_schema, "settings")
        unsupported = [f.name for f in fields if f.control == "yaml"]
        assert not unsupported, f"no widget for: {unsupported}"
        assert len(fields) > 35

    def test_every_setting_carries_help_text(self, config_schema):
        """Kometa documents its schema; the form should surface that rather than waste it."""
        fields = sf.form_model(config_schema, "settings")
        assert all(f.description for f in fields)

    def test_control_types_match_the_schema(self, config_schema):
        by_name = {f.name: f for f in sf.form_model(config_schema, "settings")}
        assert by_name["cache"].control == "boolean"
        assert by_name["cache_expiration"].control == "integer"
        assert by_name["sync_mode"].control == "select"
        assert by_name["sync_mode"].options == ["sync", "append"]
        assert by_name["asset_directory"].control == "list"

    def test_numeric_bounds_are_carried_through(self, config_schema):
        by_name = {f.name: f for f in sf.form_model(config_schema, "settings")}
        assert by_name["cache_expiration"].minimum == 1

    def test_scalar_unions_become_text_not_raw_yaml(self, config_schema):
        """`collection_section` is `string | number`; a text box accepts both."""
        by_name = {f.name: f for f in sf.form_model(config_schema, "award-template-vars")}
        assert by_name["collection_section"].control == "text"

    def test_oneof_object_definitions_are_merged(self, config_schema):
        """`operations` is a bare oneOf of variants; the form needs their union."""
        fields = sf.form_model(config_schema, "operations")
        names = {f.name for f in fields}
        assert {"assets_for_all", "delete_collections", "mass_poster_update"} <= names

    def test_collection_definition_is_mostly_renderable(self):
        """The 279-property builder surface is the hard case; keep an eye on regressions."""
        schema = validation.load_schema("collection-schema.json")
        fields = sf.form_model(schema, "collection-definition")
        renderable = sum(1 for f in fields if f.control != "yaml") / len(fields)
        assert renderable > 0.9, f"only {renderable:.0%} renderable"

    def test_unknown_definition_raises(self, config_schema):
        with pytest.raises(KeyError):
            sf.form_model(config_schema, "not-a-definition")


class TestDefaultsRoundTrip:
    def test_adding_a_default_preserves_comments_and_layout(self):
        result = documents.add_default(CONFIG, "Movies", "collection", "oscars", {"collection_section": "020"})

        assert "# My Kometa config" in result
        assert "# main film library" in result
        entry = loads(result)["libraries"]["Movies"]["collection_files"][-1]
        assert entry["default"] == "oscars"
        assert entry["template_variables"] == {"collection_section": "020"}

    def test_adding_a_default_only_adds_lines(self):
        result = documents.add_default(CONFIG, "Movies", "collection", "imdb")
        before = CONFIG.splitlines()
        after = result.splitlines()
        # Every original line survives, in order.
        assert before == [line for line in after if line in before][: len(before)]
        assert len(after) > len(before)

    def test_creates_the_file_list_when_absent(self):
        """Enabling the first default on a bare library must still work."""
        result = documents.add_default(CONFIG, "Shows", "overlay", "ribbon")
        assert loads(result)["libraries"]["Shows"]["overlay_files"] == [{"default": "ribbon"}]

    def test_rejects_enabling_the_same_default_twice(self):
        once = documents.add_default(CONFIG, "Movies", "collection", "imdb")
        with pytest.raises(EditError, match="already enabled"):
            documents.add_default(once, "Movies", "collection", "imdb")

    def test_recognises_the_legacy_pmm_key_as_enabled(self):
        """Live configs still use `pmm:`; the browser must show those as on."""
        text = (
            "libraries:\n"
            "  Movies:\n"
            "    overlay_files:\n"
            "      - pmm: ratings\n"
            "        template_variables:\n"
            "          rating1: audience\n"
        )
        enabled = documents.enabled_defaults(text)
        assert len(enabled) == 1
        assert enabled[0]["name"] == "ratings"
        assert enabled[0]["legacyKey"] is True
        assert enabled[0]["templateVariables"] == {"rating1": "audience"}

    def test_disabling_removes_only_that_entry(self):
        text = documents.add_default(CONFIG, "Movies", "collection", "oscars")
        result = documents.remove_default(text, "Movies", "collection_files", 1)
        files = loads(result)["libraries"]["Movies"]["collection_files"]
        assert files == [{"file": "config/movies.yml"}]

    def test_template_variables_can_be_added_then_replaced_then_cleared(self):
        text = documents.add_default(CONFIG, "Movies", "collection", "oscars")

        added = documents.set_default_template_variables(
            text, "Movies", "collection_files", 1, {"collection_section": "020"}
        )
        assert loads(added)["libraries"]["Movies"]["collection_files"][1]["template_variables"] == {
            "collection_section": "020"
        }

        replaced = documents.set_default_template_variables(
            added, "Movies", "collection_files", 1, {"sort_by": "random"}
        )
        assert loads(replaced)["libraries"]["Movies"]["collection_files"][1]["template_variables"] == {
            "sort_by": "random"
        }

        cleared = documents.set_default_template_variables(replaced, "Movies", "collection_files", 1, {})
        assert "template_variables" not in loads(cleared)["libraries"]["Movies"]["collection_files"][1]

    def test_unknown_library_is_rejected(self):
        with pytest.raises(EditError, match="No such library"):
            documents.add_default(CONFIG, "Anime", "collection", "imdb")


class TestCollectionCreation:
    def test_adds_to_an_existing_collections_block(self):
        text = "# my collections\ncollections:\n  Trending:\n    trakt_trending: 10\n"
        result = documents.add_collection(text, "Popular", {"tmdb_popular": 20, "sync_mode": "sync"})

        assert "# my collections" in result
        assert loads(result)["collections"]["Trending"] == {"trakt_trending": 10}
        assert loads(result)["collections"]["Popular"]["tmdb_popular"] == 20

    def test_creates_the_collections_key_when_missing(self):
        text = "templates:\n  base:\n    sync_mode: sync\n"
        result = documents.add_collection(text, "Popular", {"tmdb_popular": 20})
        assert loads(result)["collections"]["Popular"] == {"tmdb_popular": 20}
        assert loads(result)["templates"]["base"] == {"sync_mode": "sync"}

    def test_writes_a_brand_new_file(self):
        result = documents.add_collection("", "Popular", {"tmdb_popular": 20})
        assert loads(result) == {"collections": {"Popular": {"tmdb_popular": 20}}}

    def test_overlays_use_their_own_key(self):
        result = documents.add_overlay("", "Ribbon", {"overlay": "ribbon"})
        assert loads(result) == {"overlays": {"Ribbon": {"overlay": "ribbon"}}}


class TestSetValue:
    def test_updates_an_existing_scalar_in_place(self):
        result = documents.set_value(CONFIG, ["settings", "cache"], False)
        assert loads(result)["settings"]["cache"] is False
        assert "# My Kometa config" in result

    def test_adds_a_missing_key(self):
        result = documents.set_value(CONFIG, ["settings", "minimum_items"], 3)
        assert loads(result)["settings"]["minimum_items"] == 3

    def test_replaces_a_subtree(self):
        result = documents.set_value(CONFIG, ["libraries", "Movies", "collection_files"], [{"default": "imdb"}])
        assert loads(result)["libraries"]["Movies"]["collection_files"] == [{"default": "imdb"}]

    def test_removes_a_key(self):
        result = documents.remove_value(CONFIG, ["settings", "cache"])
        assert "cache" not in loads(result)["settings"]


class TestMergeMapping:
    """Saving a form. The mapping must end up matching the submitted values exactly,
    while touching as few lines as possible."""

    SETTINGS = (
        "# top\n"
        "settings:\n"
        "  cache: true          # keep this note\n"
        "  minimum_items: 1\n"
        "  sync_mode: append\n"
    )

    def test_changes_only_the_edited_key(self):
        result = documents.merge_mapping(
            self.SETTINGS,
            ["settings"],
            {"cache": True, "minimum_items": 5, "sync_mode": "append"},
        )
        before, after = self.SETTINGS.splitlines(), result.splitlines()
        differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        assert len(differing) == 1
        assert "# keep this note" in result
        assert loads(result)["settings"]["minimum_items"] == 5

    def test_adds_a_key_that_was_not_set(self):
        result = documents.merge_mapping(
            self.SETTINGS,
            ["settings"],
            {"cache": True, "minimum_items": 1, "sync_mode": "append", "show_missing": False},
        )
        assert loads(result)["settings"]["show_missing"] is False
        assert "# top" in result

    def test_clearing_a_field_removes_the_key(self):
        """A form submits the complete intended state, so an absent key means 'unset'."""
        result = documents.merge_mapping(
            self.SETTINGS, ["settings"], {"cache": True, "minimum_items": 1}
        )
        assert "sync_mode" not in loads(result)["settings"]
        assert loads(result)["settings"]["cache"] is True

    def test_no_changes_leaves_the_text_untouched(self):
        result = documents.merge_mapping(
            self.SETTINGS,
            ["settings"],
            {"cache": True, "minimum_items": 1, "sync_mode": "append"},
        )
        assert result == self.SETTINGS

    def test_creates_the_mapping_when_absent(self):
        text = "libraries:\n  Movies:\n    collection_files: []\n"
        result = documents.merge_mapping(text, ["libraries", "Movies", "operations"], {"assets_for_all": True})
        assert loads(result)["libraries"]["Movies"]["operations"] == {"assets_for_all": True}

    def test_emptying_everything_leaves_a_valid_mapping(self):
        """Removing the last key must not leave a bare `settings:` that parses as null."""
        result = documents.merge_mapping(self.SETTINGS, ["settings"], {})
        assert loads(result)["settings"] == {}
