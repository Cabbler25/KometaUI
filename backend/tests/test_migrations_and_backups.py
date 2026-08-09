"""The deprecation assistant and the change-history layer.

The migration tests carry the most weight in this file. A rewrite that gets
``delete_unmanaged_collections`` backwards would start deleting a user's Kometa-built
collections, so the direction of that mapping is pinned explicitly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services import backups, migrations
from app.services.yaml_doc import loads

CONFIG = """\
# my config
libraries:
  Movies:                       # keep me
    metadata_path:
      - file: config/old.yml
    overlay_files:
      - pmm: ratings
        template_variables:
          rating1: audience
      - default: ribbon
    operations:
      mass_poster_update: tmdb
      delete_unmanaged_collections: true
  Shows:
    overlay_path:
      - file: config/ov.yml
    operations:
      delete_unmanaged_collections: false
      delete_collections_with_less: 3
"""


def find(findings, key, library=None):
    for finding in findings:
        if finding.key == key and (library is None or library in finding.location):
            return finding
    raise AssertionError(f"no finding for {key} ({library})")


class TestDetection:
    def test_finds_renamed_library_keys(self):
        findings = migrations.scan_config(CONFIG, "config.yml")
        assert find(findings, "metadata_path").message.startswith("‘metadata_path’")
        assert find(findings, "overlay_path")

    def test_finds_legacy_pmm_entries(self):
        findings = migrations.scan_config(CONFIG, "config.yml")
        pmm = find(findings, "pmm")
        assert "ratings" in pmm.message
        # Cosmetic: Kometa still reads it, so it should not shout.
        assert pmm.severity == "info"
        assert pmm.changes_behaviour is False

    def test_a_clean_config_reports_nothing(self):
        clean = "libraries:\n  Movies:\n    collection_files:\n      - default: imdb\n"
        assert migrations.scan_config(clean, "config.yml") == []

    def test_ignores_files_that_are_not_configs(self):
        assert migrations.scan_config("collections:\n  Foo:\n    tmdb_popular: 10\n", "x.yml") == []


class TestDeleteUnmanagedSemantics:
    """`delete_unmanaged_collections` is the one rewrite that can destroy data.

    Kometa's `_should_be_deleted` computes `managed_check = managed_in == is_managed`,
    so `{managed: false}` deletes collections *without* the Kometa label. Writing
    `{managed: true}` would delete every collection Kometa built.
    """

    def test_true_maps_to_managed_false(self):
        findings = migrations.scan_config(CONFIG, "config.yml")
        finding = find(findings, "delete_unmanaged_collections", "Movies")
        assert finding.changes_behaviour is True

        result = migrations.apply_finding(CONFIG, finding)
        operations = loads(result)["libraries"]["Movies"]["operations"]
        assert operations["delete_collections"] == {"managed": False}
        assert "delete_unmanaged_collections" not in operations

    def test_false_is_removed_rather_than_translated(self):
        """Translating a disabled option into an enabled one would be catastrophic."""
        findings = migrations.scan_config(CONFIG, "config.yml")
        finding = find(findings, "delete_unmanaged_collections", "Shows")
        assert finding.changes_behaviour is False

        result = migrations.apply_finding(CONFIG, finding)
        operations = loads(result)["libraries"]["Shows"]["operations"]
        assert "delete_unmanaged_collections" not in operations
        assert "delete_collections" not in operations

    def test_the_detail_explains_that_the_key_is_currently_inert(self):
        finding = find(migrations.scan_config(CONFIG, "config.yml"), "delete_unmanaged_collections", "Movies")
        assert "no effect" in finding.detail

    def test_collections_with_less_moves_into_delete_collections(self):
        finding = find(migrations.scan_config(CONFIG, "config.yml"), "delete_collections_with_less")
        result = migrations.apply_finding(CONFIG, finding)
        operations = loads(result)["libraries"]["Shows"]["operations"]
        assert operations["delete_collections"]["less"] == 3

    def test_merges_into_an_existing_delete_collections_block(self):
        """Clobbering a block the user already wrote would silently drop criteria."""
        text = (
            "libraries:\n  Movies:\n    operations:\n"
            "      delete_collections:\n        configured: true\n"
            "      delete_collections_with_less: 5\n"
        )
        finding = find(migrations.scan_config(text, "config.yml"), "delete_collections_with_less")
        result = migrations.apply_finding(text, finding)
        block = loads(result)["libraries"]["Movies"]["operations"]["delete_collections"]
        assert block == {"configured": True, "less": 5}


class TestApplying:
    def test_renames_happen_in_place(self):
        """A rename should not relocate the entry; that would be a needlessly big diff."""
        findings = migrations.scan_config(CONFIG, "config.yml")
        result = migrations.apply_finding(CONFIG, find(findings, "metadata_path"))
        lines = [line.strip() for line in result.splitlines()]
        assert lines.index("collection_files:") < lines.index("overlay_files:")

    def test_preserves_comments_and_siblings(self):
        safe = [f.id for f in migrations.scan_config(CONFIG, "config.yml") if not f.changes_behaviour]
        result, applied = migrations.scan_and_apply(CONFIG, "config.yml", safe)

        assert len(applied) == len(safe)
        assert "# my config" in result
        assert "# keep me" in result
        entry = loads(result)["libraries"]["Movies"]["overlay_files"][0]
        assert entry["default"] == "ratings"
        assert entry["template_variables"] == {"rating1": "audience"}

    def test_applying_everything_leaves_nothing_to_find(self):
        all_ids = [f.id for f in migrations.scan_config(CONFIG, "config.yml")]
        result, applied = migrations.scan_and_apply(CONFIG, "config.yml", all_ids)
        assert len(applied) == len(all_ids)
        assert migrations.scan_config(result, "config.yml") == []

    def test_batches_rescan_between_edits(self):
        """Positions shift after every edit, so a batch computed once would corrupt."""
        ids = [f.id for f in migrations.scan_config(CONFIG, "config.yml")]
        result, _ = migrations.scan_and_apply(CONFIG, "config.yml", ids)
        assert loads(result), "batch application produced unparseable YAML"


class TestBackups:
    def test_lists_newest_first_with_readable_timestamps(self, tmp_path: Path):
        target = tmp_path / "config.yml"
        target.write_text("v: 1\n", encoding="utf-8")
        from app.services.yaml_doc import write_with_backup

        write_with_backup(target, "v: 2\n")
        write_with_backup(target, "v: 3\n")

        found = backups.list_backups(target)
        assert len(found) == 2
        assert found[0].stamp >= found[1].stamp
        assert found[0].taken_at.startswith("20")

    def test_rapid_saves_do_not_overwrite_each_other(self, tmp_path: Path):
        """Second-resolution stamps collided, silently discarding the earlier version."""
        target = tmp_path / "config.yml"
        target.write_text("v: 0\n", encoding="utf-8")
        from app.services.yaml_doc import write_with_backup

        for i in range(1, 6):
            write_with_backup(target, f"v: {i}\n")

        found = backups.list_backups(target)
        assert len(found) == 5, "backups written in the same second overwrote one another"
        assert len({b.stamp for b in found}) == 5

    def test_reads_stamps_written_before_millisecond_precision(self, tmp_path: Path):
        """History taken by an older build must stay visible."""
        from app.services.yaml_doc import BACKUP_SUFFIX

        target = tmp_path / "config.yml"
        target.write_text("v: 1\n", encoding="utf-8")
        (tmp_path / BACKUP_SUFFIX).mkdir()
        (tmp_path / BACKUP_SUFFIX / "config.yml.20250101-120000").write_text("old\n", encoding="utf-8")

        found = backups.list_backups(target)
        assert [b.stamp for b in found] == ["20250101-120000"]
        assert found[0].taken_at == "2025-01-01T12:00:00"

    def test_ignores_unrelated_files_in_the_backup_directory(self, tmp_path: Path):
        target = tmp_path / "config.yml"
        target.write_text("v: 1\n", encoding="utf-8")
        from app.services.yaml_doc import BACKUP_SUFFIX, write_with_backup

        write_with_backup(target, "v: 2\n")
        (tmp_path / BACKUP_SUFFIX / "config.yml.notastamp").write_text("junk", encoding="utf-8")

        assert len(backups.list_backups(target)) == 1

    def test_restore_is_itself_undoable(self, tmp_path: Path):
        target = tmp_path / "config.yml"
        target.write_text("v: 1\n", encoding="utf-8")
        from app.services.yaml_doc import write_with_backup

        write_with_backup(target, "v: 2\n")
        stamp = backups.list_backups(target)[0].stamp

        backups.restore(target, stamp)
        assert target.read_text(encoding="utf-8") == "v: 1\n"
        # The pre-restore state was captured, so the restore can be walked back.
        assert any(backups.read_backup(target, b.stamp) == "v: 2\n" for b in backups.list_backups(target))

    def test_missing_backup_raises(self, tmp_path: Path):
        target = tmp_path / "config.yml"
        target.write_text("v: 1\n", encoding="utf-8")
        with pytest.raises(FileNotFoundError):
            backups.read_backup(target, "20200101-000000")


class TestDiff:
    def test_identical_text_produces_no_diff(self):
        assert backups.unified_diff("a\n", "a\n", "x.yml") == []

    def test_counts_added_and_removed(self):
        diff = backups.unified_diff("a\nb\n", "a\nc\nd\n", "x.yml")
        assert backups.diff_stats(diff) == {"added": 2, "removed": 1}

    def test_headers_are_not_counted_as_changes(self):
        diff = backups.unified_diff("a\n", "b\n", "x.yml")
        assert any(line.startswith("---") for line in diff)
        assert backups.diff_stats(diff) == {"added": 1, "removed": 1}
