"""Surgical edit behaviour.

The contract these tests defend: an edit changes the lines it must and nothing else.
That is what makes form-driven editing safe on a hand-written config.
"""

from __future__ import annotations

import difflib
from pathlib import Path as FsPath

import pytest

from app.services.yaml_doc import loads
from app.services.yaml_edit import (
    EditError,
    append_sequence_item,
    delete_node,
    insert_mapping_entry,
    node_span,
    render_fragment,
    replace_node,
    set_scalar,
)

CONFIG = """\
# Kometa configuration
libraries:
  Movies:                          # my film library
    collection_files:
      - file: config/movies.yml
      - default: imdb
    operations:
      mass_poster_update: tmdb
  Shows:
    collection_files:
      - default: tmdb

settings:
  cache: true
  minimum_items: 1
  # how long cached data survives
  cache_expiration: 60
"""


def changed_lines(before: str, after: str) -> list[str]:
    """The +/- lines of a diff, for asserting on blast radius."""
    diff = difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
    return [line for line in diff if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]


class TestNodeSpan:
    def test_locates_a_nested_mapping(self):
        span = node_span(CONFIG, ["libraries", "Movies"])
        lines = CONFIG.splitlines()
        assert lines[span.start].strip().startswith("Movies:")
        # Runs up to, but not including, the Shows key.
        assert lines[span.end].strip().startswith("Shows:")

    def test_locates_a_sequence_item(self):
        span = node_span(CONFIG, ["libraries", "Movies", "collection_files", 0])
        assert CONFIG.splitlines()[span.start].strip() == "- file: config/movies.yml"
        assert span.end == span.start + 1

    def test_excludes_trailing_blank_lines(self):
        span = node_span(CONFIG, ["libraries"])
        # The blank line before `settings:` must not be swallowed.
        assert CONFIG.splitlines()[span.end - 1].strip() == "- default: tmdb"

    def test_rejects_unknown_path(self):
        with pytest.raises(EditError, match="No such path"):
            node_span(CONFIG, ["libraries", "Nope"])


class TestSetScalar:
    def test_changes_exactly_one_line(self):
        result = set_scalar(CONFIG, ["settings", "minimum_items"], 5)
        assert changed_lines(CONFIG, result) == ["-  minimum_items: 1", "+  minimum_items: 5"]
        assert loads(result)["settings"]["minimum_items"] == 5

    def test_preserves_an_inline_comment(self):
        text = "settings:\n  cache: true   # keep this note\n"
        result = set_scalar(text, ["settings", "cache"], False)
        assert "# keep this note" in result
        assert loads(result)["settings"]["cache"] is False

    def test_writes_booleans_in_yaml_form(self):
        result = set_scalar(CONFIG, ["settings", "cache"], False)
        assert "cache: false" in result
        assert loads(result)["settings"]["cache"] is False

    def test_leaves_every_other_line_untouched(self):
        result = set_scalar(CONFIG, ["settings", "cache_expiration"], 90)
        before, after = CONFIG.splitlines(), result.splitlines()
        assert len(before) == len(after)
        differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        assert len(differing) == 1

    def test_refuses_a_non_scalar(self):
        with pytest.raises(EditError, match="not a scalar"):
            set_scalar(CONFIG, ["settings"], 1)


class TestInsertMappingEntry:
    def test_adds_a_collection_without_touching_existing_ones(self):
        text = "collections:\n  Trending:\n    trakt_trending: 10\n"
        result = insert_mapping_entry(text, ["collections"], "Popular", {"tmdb_popular": 20})

        assert "Trending:" in result and "trakt_trending: 10" in result
        assert loads(result)["collections"]["Popular"] == {"tmdb_popular": 20}
        # Only additions, no removals.
        assert all(line.startswith("+") for line in changed_lines(text, result))

    def test_matches_sibling_indentation(self):
        result = insert_mapping_entry(CONFIG, ["libraries"], "Anime", {"collection_files": []})
        assert "\n  Anime:\n" in result
        assert loads(result)["libraries"]["Anime"] == {"collection_files": []}

    def test_preserves_comments_elsewhere(self):
        result = insert_mapping_entry(CONFIG, ["settings"], "sync_mode", "sync")
        assert "# Kometa configuration" in result
        assert "# how long cached data survives" in result
        assert "# my film library" in result

    def test_populates_an_empty_flow_mapping(self):
        text = "libraries:\n  Movies: {}\n"
        result = insert_mapping_entry(text, ["libraries", "Movies"], "library_type", "movie")
        assert loads(result)["libraries"]["Movies"] == {"library_type": "movie"}
        assert "{}" not in result

    def test_rejects_a_duplicate_key(self):
        with pytest.raises(EditError, match="already exists"):
            insert_mapping_entry(CONFIG, ["settings"], "cache", False)

    def test_rejects_a_non_mapping_target(self):
        with pytest.raises(EditError, match="not a mapping"):
            insert_mapping_entry(CONFIG, ["libraries", "Movies", "collection_files"], "x", 1)


class TestAppendSequenceItem:
    def test_adds_a_default_to_a_file_list(self):
        """The core 'enable a Kometa default' operation."""
        result = append_sequence_item(
            CONFIG, ["libraries", "Movies", "collection_files"], {"default": "oscars"}
        )
        files = loads(result)["libraries"]["Movies"]["collection_files"]
        assert files[-1] == {"default": "oscars"}
        assert len(files) == 3
        assert all(line.startswith("+") for line in changed_lines(CONFIG, result))

    def test_matches_existing_dash_indentation(self):
        result = append_sequence_item(
            CONFIG, ["libraries", "Movies", "collection_files"], {"default": "oscars"}
        )
        dashes = {
            len(line) - len(line.lstrip())
            for line in result.splitlines()
            if line.strip().startswith("- default:") or line.strip().startswith("- file:")
        }
        assert len(dashes) == 1, f"inconsistent indentation: {dashes}"

    def test_supports_template_variables(self):
        result = append_sequence_item(
            CONFIG,
            ["libraries", "Movies", "collection_files"],
            {"default": "imdb", "template_variables": {"collection_section": "020"}},
        )
        added = loads(result)["libraries"]["Movies"]["collection_files"][-1]
        assert added["template_variables"] == {"collection_section": "020"}

    def test_populates_an_empty_list(self):
        text = "libraries:\n  Movies:\n    collection_files: []\n"
        result = append_sequence_item(text, ["libraries", "Movies", "collection_files"], {"default": "imdb"})
        assert loads(result)["libraries"]["Movies"]["collection_files"] == [{"default": "imdb"}]

    def test_rejects_a_non_sequence(self):
        with pytest.raises(EditError, match="not a sequence"):
            append_sequence_item(CONFIG, ["settings"], 1)


class TestDeleteNode:
    def test_removes_a_mapping_entry(self):
        result = delete_node(CONFIG, ["libraries", "Shows"])
        assert "Shows" not in loads(result)["libraries"]
        assert "Movies" in loads(result)["libraries"]

    def test_removes_a_sequence_item(self):
        result = delete_node(CONFIG, ["libraries", "Movies", "collection_files", 0])
        assert loads(result)["libraries"]["Movies"]["collection_files"] == [{"default": "imdb"}]

    def test_takes_the_comment_that_documents_it(self):
        result = delete_node(CONFIG, ["settings", "cache_expiration"])
        assert "# how long cached data survives" not in result
        # Unrelated comments survive.
        assert "# Kometa configuration" in result


class TestReplaceNode:
    def test_swaps_a_subtree_in_place(self):
        result = replace_node(CONFIG, ["libraries", "Movies", "operations"], {"assets_for_all": True})
        assert loads(result)["libraries"]["Movies"]["operations"] == {"assets_for_all": True}
        assert loads(result)["libraries"]["Shows"] is not None


class TestRenderFragment:
    def test_indents_a_nested_block(self):
        fragment = render_fragment({"a": {"b": 1}}, indent=4, key="outer")
        assert fragment.startswith("    outer:")
        assert "\n      a:" in fragment

    def test_renders_a_sequence(self):
        assert render_fragment([{"default": "imdb"}]).strip() == "- default: imdb"


class TestAgainstRealFiles:
    """Exercise the editor on Kometa's own files, which are far messier than fixtures."""

    def test_insertions_never_disturb_existing_lines(self, real_yaml_files: list[FsPath]):
        checked = 0
        for path in real_yaml_files:
            text = path.read_text(encoding="utf-8")
            data = loads(text)
            if not isinstance(data, dict) or "collections" not in data:
                continue
            if not isinstance(data["collections"], dict) or not data["collections"]:
                continue

            result = insert_mapping_entry(
                text, ["collections"], "KometaUI Probe Collection", {"tmdb_popular": 10}
            )
            # Every original line must still be present, in order, unmodified.
            original = text.splitlines()
            produced = result.splitlines()
            assert all(line.startswith("+") for line in changed_lines(text, result)), (
                f"insertion modified existing lines in {path.name}"
            )
            assert len(produced) > len(original)
            assert loads(result)["collections"]["KometaUI Probe Collection"] == {"tmdb_popular": 10}
            checked += 1

        assert checked > 20, f"expected a broad sample, only exercised {checked} files"

    def test_scalar_edits_change_a_single_line(self, real_yaml_files: list[FsPath]):
        """Kometa's files are mappings of mappings, so hunt one level down for a scalar."""
        checked = 0
        for path in real_yaml_files:
            text = path.read_text(encoding="utf-8")
            data = loads(text)
            if not isinstance(data, dict):
                continue

            target: list | None = None
            for key, value in data.items():
                if isinstance(value, dict):
                    for inner_key, inner in value.items():
                        if isinstance(inner, (str, int, float, bool)):
                            target = [key, inner_key]
                            break
                if target:
                    break
            if target is None:
                continue

            result = set_scalar(text, target, "kometaui-probe")
            assert len(changed_lines(text, result)) == 2, f"blast radius too wide in {path.name}"
            assert loads(result), f"produced unparseable YAML for {path.name}"
            checked += 1

        assert checked > 20, f"expected a broad sample, only exercised {checked} files"
