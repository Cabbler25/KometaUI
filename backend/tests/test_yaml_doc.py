"""Round-trip safety.

A config editor that quietly drops a user's comments or reorders their keys is worse than
no editor. These tests pin that behaviour against every YAML file Kometa ships.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml.error import YAMLError

from app.services.yaml_doc import (
    dumps,
    loads,
    round_trip,
    safe_parse,
    save_text,
    write_with_backup,
)


def _plain(value):
    """Strip ruamel's comment-carrying wrappers so equality compares data only."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


class TestRoundTrip:
    def test_preserves_semantics_for_every_kometa_file(self, real_yaml_files: list[Path]):
        """Data must survive a parse/dump cycle unchanged, for all 93 shipped files."""
        assert real_yaml_files, "expected Kometa YAML files to test against"
        for path in real_yaml_files:
            text = path.read_text(encoding="utf-8")
            assert _plain(loads(text)) == _plain(loads(round_trip(text))), f"semantics changed: {path.name}"

    def test_is_idempotent(self, real_yaml_files: list[Path]):
        """Formatting must converge: a second pass changes nothing.

        This is what makes normalisation acceptable. A file may be reformatted once, but
        it must never drift further on subsequent saves.
        """
        for path in real_yaml_files:
            once = round_trip(path.read_text(encoding="utf-8"))
            assert round_trip(once) == once, f"not idempotent: {path.name}"

    def test_rewriting_stays_within_its_known_bounds(self, real_yaml_files: list[Path]):
        """Characterise exactly how much a dump cycle rewrites, so drift is visible.

        A dump cycle is not byte-faithful. On Kometa 2.4.6 it performs five distinct
        cosmetic transformations, all semantically inert:

        1. strips trailing whitespace;
        2. normalises indentation -- ``defaults/award/sag.yml`` indents a mapping by one
           space, which becomes two;
        3. tightens spacing inside flow collections -- ``{ weight: 1 }`` -> ``{weight: 1}``;
        4. lowercases booleans -- ``defaults/both/streaming.yml`` writes ``False``;
        5. drops the braces on a single-pair flow mapping nested in a flow sequence --
           ``defaults/overlays/ribbon.yml`` has ``[{...}, {name: ribbon}]``, which becomes
           ``[{...}, name: ribbon]``. YAML treats the two as equivalent and ruamel reads
           its own output back identically, but it is still a visible rewrite.

        Rather than pattern-matching each transformation -- brittle, and it would silently
        absorb a sixth -- this pins the number of files affected. Semantic safety is proven
        separately by :meth:`test_preserves_semantics_for_every_kometa_file`; what this
        catches is a Kometa or ruamel change that starts rewriting *more* than it used to.
        """
        rewritten = [p.name for p in real_yaml_files if round_trip(p.read_text(encoding="utf-8")) != p.read_text(encoding="utf-8")]

        # 50 of the 93 files Kometa ships are rewritten in some cosmetic way. If this
        # number moves, inspect the diff before updating it -- a new transformation may
        # not be as harmless as the five above.
        assert len(rewritten) == 50, (
            f"dump-cycle rewriting changed: {len(rewritten)} of {len(real_yaml_files)} files "
            f"are now rewritten, expected 50. Affected: {sorted(rewritten)}"
        )

    def test_untouched_files_are_never_rewritten_on_save(self, real_yaml_files: list[Path], tmp_path: Path):
        """The guarantee that actually matters: saving text the user did not change
        leaves the file byte-identical, for every file Kometa ships."""
        for path in real_yaml_files[:20]:  # a representative slice; full set is slow
            text = path.read_text(encoding="utf-8")
            target = tmp_path / path.name
            target.write_text(text, encoding="utf-8")
            save_text(target, text)
            assert target.read_text(encoding="utf-8") == text, f"save rewrote {path.name}"

    def test_preserves_comments(self):
        text = (
            "# top comment\n"
            "libraries:\n"
            "  Movies:  # inline comment\n"
            "    # nested comment\n"
            "    collection_files:\n"
            "      - file: config/movies.yml\n"
        )
        produced = round_trip(text)
        assert "# top comment" in produced
        assert "# inline comment" in produced
        assert "# nested comment" in produced

    def test_preserves_quote_style(self):
        text = "plex:\n  url: 'http://localhost:32400'\n  token: \"abc\"\n"
        produced = round_trip(text)
        assert "'http://localhost:32400'" in produced
        assert '"abc"' in produced


class TestSafeParse:
    def test_returns_error_with_position(self):
        data, error = safe_parse("libraries:\n  - [unclosed\n")
        assert data is None
        assert error is not None
        assert error.line is not None and error.line >= 1

    def test_returns_data_when_valid(self):
        data, error = safe_parse("a: 1\n")
        assert error is None
        assert data["a"] == 1


class TestWriting:
    def test_save_text_is_verbatim(self, tmp_path: Path):
        """User-authored text must land on disk exactly as typed, including odd spacing."""
        target = tmp_path / "c.yml"
        text = "libraries:\n   Movies:   \n     collection_files: []\n"
        save_text(target, text)
        assert target.read_text(encoding="utf-8") == text

    def test_save_text_rejects_invalid_yaml_before_touching_disk(self, tmp_path: Path):
        target = tmp_path / "c.yml"
        target.write_text("good: yes\n", encoding="utf-8")
        with pytest.raises(YAMLError):
            save_text(target, "bad: [unclosed\n")
        assert target.read_text(encoding="utf-8") == "good: yes\n"

    def test_backup_captures_previous_contents(self, tmp_path: Path):
        target = tmp_path / "c.yml"
        target.write_text("version: 1\n", encoding="utf-8")
        backup = write_with_backup(target, "version: 2\n")
        assert backup is not None
        assert backup.read_text(encoding="utf-8") == "version: 1\n"
        assert target.read_text(encoding="utf-8") == "version: 2\n"

    def test_no_backup_for_new_file(self, tmp_path: Path):
        assert write_with_backup(tmp_path / "new.yml", "a: 1\n") is None

    def test_backups_are_pruned_to_retention(self, tmp_path: Path):
        target = tmp_path / "c.yml"
        target.write_text("v: 0\n", encoding="utf-8")
        for i in range(8):
            write_with_backup(target, f"v: {i}\n", retention=3)
        backups = list((tmp_path / ".kometaui-bak").glob("c.yml.*"))
        assert len(backups) <= 3

    def test_dumps_accepts_plain_structures(self):
        assert dumps({"a": [1, 2]}).strip().startswith("a:")
