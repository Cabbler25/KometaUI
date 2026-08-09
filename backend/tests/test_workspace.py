"""Workspace scoping, reference resolution, and the read-only guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.workspace import ReadOnlyError, Workspace, WorkspaceError


class TestPathSafety:
    @pytest.mark.parametrize(
        "attempt",
        ["../outside.yml", "../../etc/passwd", "sub/../../escape.yml", "..\\windows.yml"],
    )
    def test_rejects_paths_that_escape_the_root(self, workspace: Workspace, attempt: str):
        with pytest.raises(WorkspaceError, match="escapes"):
            workspace.resolve(attempt)

    def test_allows_paths_inside_the_root(self, workspace: Workspace):
        assert workspace.resolve("config.yml").name == "config.yml"
        assert workspace.resolve("nested/deep/file.yml").name == "file.yml"

    def test_root_itself_resolves(self, workspace: Workspace):
        assert workspace.resolve("") == workspace.root

    def test_rejects_a_file_as_root(self, tmp_path: Path):
        target = tmp_path / "f.yml"
        target.write_text("a: 1\n", encoding="utf-8")
        with pytest.raises(WorkspaceError):
            Workspace(target)


class TestReadOnlyGuard:
    def test_writes_are_refused_by_default(self, tmp_path: Path):
        (tmp_path / "c.yml").write_text("a: 1\n", encoding="utf-8")
        readonly = Workspace(tmp_path)  # allow_writes defaults to False
        with pytest.raises(ReadOnlyError):
            readonly.write("c.yml", "a: 2\n")
        assert (tmp_path / "c.yml").read_text(encoding="utf-8") == "a: 1\n"

    def test_writes_succeed_once_unlocked(self, workspace: Workspace):
        workspace.write("config.yml", "libraries:\n  Shows: {}\n")
        assert "Shows" in workspace.read("config.yml")

    def test_invalid_yaml_is_refused(self, workspace: Workspace):
        original = workspace.read("config.yml")
        with pytest.raises(Exception):
            workspace.write("config.yml", "bad: [unclosed\n")
        assert workspace.read("config.yml") == original


class TestTree:
    def test_lists_yaml_and_skips_noise(self, tmp_path: Path):
        (tmp_path / "a.yml").write_text("collections: {}\n", encoding="utf-8")
        (tmp_path / "notes.txt").write_text("hi", encoding="utf-8")
        (tmp_path / "logs").mkdir()
        (tmp_path / "logs" / "meta.yml").write_text("x: 1\n", encoding="utf-8")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.yaml").write_text("overlays: {}\n", encoding="utf-8")

        tree = Workspace(tmp_path).tree()
        names = {c.name for c in tree.children}
        assert "a.yml" in names
        assert "notes.txt" not in names, "non-YAML should be hidden"
        assert "logs" not in names, "generated directories should be hidden"
        assert "sub" in names

    def test_hides_directories_with_no_yaml(self, tmp_path: Path):
        (tmp_path / "images").mkdir()
        (tmp_path / "images" / "poster.png").write_bytes(b"x")
        assert Workspace(tmp_path).tree().children == []


class TestConfigCandidates:
    def test_finds_every_config_and_ranks_the_conventional_one_first(self, tmp_path: Path):
        # A real install keeps several configs side by side and picks with --config.
        (tmp_path / "config.yml").write_text("libraries:\n  Movies: {}\n", encoding="utf-8")
        (tmp_path / "config_posters.yml").write_text("libraries:\n  Movies: {}\n  Shows: {}\n", encoding="utf-8")
        (tmp_path / "movies.yml").write_text("collections:\n  Foo: {}\n", encoding="utf-8")

        candidates = Workspace(tmp_path).config_candidates()
        paths = [c["path"] for c in candidates]
        assert paths[0] == "config.yml", "config.yml is Kometa's default and should lead"
        assert "config_posters.yml" in paths
        assert "movies.yml" not in paths, "collection files are not configs"
        assert candidates[0]["libraries"] == ["Movies"]

    def test_ignores_unparseable_files(self, tmp_path: Path):
        (tmp_path / "broken.yml").write_text("libraries: [unclosed\n", encoding="utf-8")
        assert Workspace(tmp_path).config_candidates() == []


class TestReferences:
    def test_resolves_local_files_relative_to_the_kometa_root(self, tmp_path: Path):
        """Kometa resolves ``config/x.yml`` from its own root, so a workspace opened at
        ``<root>/config`` must still find the file."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "movies.yml").write_text("collections: {}\n", encoding="utf-8")
        (config_dir / "config.yml").write_text(
            "libraries:\n"
            "  Movies:\n"
            "    collection_files:\n"
            "      - file: config/movies.yml\n"
            "      - file: config/absent.yml\n",
            encoding="utf-8",
        )

        refs = Workspace(config_dir).references("config.yml")
        by_value = {r.value: r for r in refs}
        assert by_value["config/movies.yml"].exists is True
        assert by_value["config/absent.yml"].exists is False

    def test_understands_both_default_and_legacy_pmm_keys(self, tmp_path: Path):
        """``pmm:`` is the pre-rename spelling and still appears in live configs."""
        (tmp_path / "config.yml").write_text(
            "libraries:\n"
            "  Movies:\n"
            "    overlay_files:\n"
            "      - default: ribbon\n"
            "      - pmm: ratings\n"
            "        template_variables:\n"
            "          rating1: audience\n",
            encoding="utf-8",
        )
        refs = Workspace(tmp_path).references("config.yml")
        kinds = {r.kind: r for r in refs}
        assert kinds["default"].value == "ribbon"
        assert kinds["pmm"].value == "ratings"
        assert kinds["pmm"].template_variables == {"rating1": "audience"}

    def test_captures_top_level_playlist_files(self, tmp_path: Path):
        (tmp_path / "config.yml").write_text(
            "libraries:\n  Movies: {}\nplaylist_files:\n  - default: playlist\n",
            encoding="utf-8",
        )
        refs = Workspace(tmp_path).references("config.yml")
        playlists = [r for r in refs if r.list_key == "playlist_files"]
        assert len(playlists) == 1
        assert playlists[0].library is None
