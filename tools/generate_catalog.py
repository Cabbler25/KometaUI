#!/usr/bin/env python3
"""Generate KometaUI's catalog of Kometa knowledge from Kometa's own source.

Kometa's JSON schemas describe *what is valid*, but they are flat: a collection
definition exposes 279 sibling properties with no grouping. The information needed to
present that space to a human -- which properties are builders, which service each
builder belongs to, which are movie-only -- lives in Python constants inside Kometa's
modules.

This script lifts those constants out and writes them to ``catalog.json``.

It reads the source with :mod:`ast` rather than importing it. Importing ``modules.builder``
requires Kometa's entire dependency tree (arrapi, tmdbapis, letterboxdpy, ...), while
parsing needs nothing but the files. That keeps the generator runnable against any Kometa
checkout, with no environment setup.

Usage::

    python tools/generate_catalog.py --kometa-source /path/to/Kometa
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------------------
# Source reading
# --------------------------------------------------------------------------------------

# Kometa modules that each declare a top-level ``builders`` list. Order mirrors the
# concatenation in modules/builder.py::all_builders so the output is stable and
# comparable against the source.
SERVICE_MODULES = [
    "anidb",
    "anilist",
    "icheckmovies",
    "imdb",
    "letterboxd",
    "mal",
    "mojo",
    "plex",
    "stevenlu",
    "tautulli",
    "textfile",
    "tmdb",
    "trakt",
    "tvdb",
    "yamtrack",
    "mdblist",
    "simkl",
    "radarr",
    "sonarr",
]

# Human-facing service labels. Anything not listed falls back to a title-cased name.
SERVICE_LABELS = {
    "anidb": "AniDB",
    "anilist": "AniList",
    "icheckmovies": "iCheckMovies",
    "imdb": "IMDb",
    "letterboxd": "Letterboxd",
    "mal": "MyAnimeList",
    "mdblist": "MDBList",
    "mojo": "Box Office Mojo",
    "plex": "Plex",
    "radarr": "Radarr",
    "simkl": "Simkl",
    "sonarr": "Sonarr",
    "stevenlu": "StevenLu",
    "tautulli": "Tautulli",
    "textfile": "Text File",
    "tmdb": "TMDb",
    "trakt": "Trakt",
    "tvdb": "TVDb",
    "yamtrack": "YamTrack",
}

BUILDER_CONSTANTS = {
    "all_builders": "all",
    "movie_only_builders": "movie_only",
    "show_only_builders": "show_only",
    "music_only_builders": "music_only",
    "custom_sort_builders": "custom_sort",
    "none_builders": "none",
    "parts_collection_valid": "parts_collection_valid",
}

DETAIL_CONSTANTS = {
    "details": "all",
    "poster_details": "poster",
    "background_details": "background",
    "logo_details": "logo",
    "square_art_details": "square_art",
    "summary_details": "summary",
    "boolean_details": "boolean",
    "string_details": "string",
    "scheduled_boolean": "scheduled_boolean",
    "collectionless_details": "collectionless",
}

PLEX_CONSTANTS = [
    "builders",
    "search_translation",
    "show_translation",
    "modifier_translation",
    "attribute_translation",
    "method_alias",
    "modifier_alias",
    "and_searches",
    "or_searches",
    "movie_only_searches",
    "show_only_searches",
    "string_attributes",
    "string_modifiers",
    "boolean_attributes",
    "tmdb_attributes",
    "date_attributes",
    "year_attributes",
    "number_attributes",
    "float_attributes",
    "tag_attributes",
    "sort_types",
    "library_types",
    "collection_order_options",
    "collection_filtering_options",
    "collection_mode_options",
    "album_sorting_options",
    "episode_sorting_options",
    "keep_episodes_options",
    "delete_episodes_options",
    "season_display_options",
    "episode_ordering_options",
    "plex_languages",
    "use_original_title_options",
    "credits_detection_options",
    "subtitle_mode_options",
    "builder_level_show_options",
    "builder_level_music_options",
]


class Unresolved(Exception):
    """Raised when a constant cannot be evaluated statically."""


@dataclass
class SourceReader:
    """Lazily parses Kometa modules and evaluates their top-level constants.

    Kometa builds several constants by combining others, both within a module
    (``details = [...] + boolean_details + string_details``) and across modules
    (``all_builders = anidb.builders + anilist.builders + ...``). A plain
    ``ast.literal_eval`` handles 17 of the 19 service modules but fails on those
    compositions, so we evaluate the small expression subset Kometa actually uses.
    """

    root: Path
    _envs: dict[str, dict[str, ast.expr]] = field(default_factory=dict)
    _cache: dict[tuple[str, str], Any] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)

    def _assignments(self, module: str) -> dict[str, ast.expr]:
        """Return a name -> expression map of a module's top-level assignments."""
        if module not in self._envs:
            path = self.root / "modules" / f"{module}.py"
            if not path.exists():
                raise FileNotFoundError(f"Kometa module not found: {path}")
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            env: dict[str, ast.expr] = {}
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            env[target.id] = node.value
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    if node.value is not None:
                        env[node.target.id] = node.value
            self._envs[module] = env
        return self._envs[module]

    def get(self, module: str, name: str) -> Any:
        """Evaluate ``module.name``, memoising the result."""
        key = (module, name)
        if key in self._cache:
            return self._cache[key]
        env = self._assignments(module)
        if name not in env:
            raise Unresolved(f"{module}.{name} is not a top-level assignment")
        value = self._eval(env[name], module, {})
        self._cache[key] = value
        return value

    def try_get(self, module: str, name: str) -> Any | None:
        """Evaluate ``module.name``, recording rather than raising on failure."""
        try:
            return self.get(module, name)
        except (Unresolved, FileNotFoundError, ValueError, KeyError) as exc:
            self.unresolved.append(f"{module}.{name}: {exc}")
            return None

    def _eval(self, node: ast.expr, module: str, scope: dict[str, Any]) -> Any:
        """Evaluate the expression subset Kometa uses for its module-level constants.

        ``scope`` carries comprehension loop variables; it is empty at the top level.
        """
        ev = lambda n: self._eval(n, module, scope)  # noqa: E731 - local shorthand

        # Plain literals: strings, numbers, True/False/None.
        if isinstance(node, ast.Constant):
            return node.value

        # A bare name is either a comprehension variable or another module constant.
        if isinstance(node, ast.Name):
            if node.id in scope:
                return scope[node.id]
            return self.get(module, node.id)

        # ``other_module.builders`` -- Kometa imports service modules by name, so the
        # attribute's value is the referenced module's constant.
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in scope:
                raise Unresolved(f"attribute access on local {node.value.id}")
            return self.get(node.value.id, node.attr)

        # ``-1`` and friends, used throughout Plex's option maps.
        if isinstance(node, ast.UnaryOp):
            operand = ev(node.operand)
            if isinstance(node.op, ast.USub):
                return -operand
            if isinstance(node.op, ast.UAdd):
                return +operand
            if isinstance(node.op, ast.Not):
                return not operand
            raise Unresolved(f"unsupported unary op {type(node.op).__name__}")

        # ``a + b`` list/tuple concatenation.
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = ev(node.left)
            right = ev(node.right)
            if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
                return list(left) + list(right)
            return left + right

        # Sequences, which may splat other constants via ``*name``.
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            out: list[Any] = []
            for element in node.elts:
                if isinstance(element, ast.Starred):
                    out.extend(ev(element.value))
                else:
                    out.append(ev(element))
            return out

        # Mappings, which may splat via ``**name``.
        if isinstance(node, ast.Dict):
            out_map: dict[Any, Any] = {}
            for key_node, value_node in zip(node.keys, node.values):
                if key_node is None:  # ``**other``
                    out_map.update(ev(value_node))
                else:
                    out_map[ev(key_node)] = ev(value_node)
            return out_map

        # f-strings, e.g. ``f"{d}_details"``.
        if isinstance(node, ast.JoinedStr):
            return "".join(
                str(ev(part.value)) if isinstance(part, ast.FormattedValue) else str(ev(part))
                for part in node.values
            )

        # ``str.lower()`` / ``str.upper()`` -- the only calls Kometa uses in these
        # constants, for building case-insensitive language lookups.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and not node.args:
            target = ev(node.func.value)
            if isinstance(target, str) and node.func.attr in ("lower", "upper", "strip", "title"):
                return getattr(target, node.func.attr)()
            raise Unresolved(f"unsupported call .{node.func.attr}()")

        # Single-generator comprehensions, e.g. ``[f"{d}_details" for d in info_builders]``
        # and ``{lang.lower(): lang for lang in plex_languages}``.
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp)):
            return self._eval_comprehension(node, module, scope)

        raise Unresolved(f"unsupported expression {type(node).__name__}")

    def _eval_comprehension(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp,
        module: str,
        scope: dict[str, Any],
    ) -> Any:
        """Evaluate a comprehension with one generator over a resolvable iterable."""
        if len(node.generators) != 1:
            raise Unresolved("only single-generator comprehensions are supported")
        gen = node.generators[0]
        if not isinstance(gen.target, ast.Name):
            raise Unresolved("only simple comprehension targets are supported")

        iterable = self._eval(gen.iter, module, scope)
        var = gen.target.id
        results_list: list[Any] = []
        results_map: dict[Any, Any] = {}

        for item in iterable:
            inner = {**scope, var: item}
            if any(not self._eval(cond, module, inner) for cond in gen.ifs):
                continue
            if isinstance(node, ast.DictComp):
                results_map[self._eval(node.key, module, inner)] = self._eval(node.value, module, inner)
            else:
                results_list.append(self._eval(node.elt, module, inner))

        return results_map if isinstance(node, ast.DictComp) else results_list


# --------------------------------------------------------------------------------------
# Catalog assembly
# --------------------------------------------------------------------------------------


def _dedupe(values: list[str]) -> list[str]:
    """Order-preserving de-duplication."""
    seen: set[str] = set()
    out = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def build_builders_by_service(reader: SourceReader) -> dict[str, dict[str, Any]]:
    """Group every builder under the service module that declares it."""
    services: dict[str, dict[str, Any]] = {}
    for module in SERVICE_MODULES:
        builders = reader.try_get(module, "builders")
        if not isinstance(builders, list):
            continue
        services[module] = {
            "label": SERVICE_LABELS.get(module, module.title()),
            "builders": _dedupe([b for b in builders if isinstance(b, str)]),
        }
    return services


def build_constant_group(reader: SourceReader, module: str, mapping: dict[str, str]) -> dict[str, list[str]]:
    """Evaluate a set of list constants into ``{friendly_key: [values]}``."""
    out: dict[str, list[str]] = {}
    for source_name, key in mapping.items():
        value = reader.try_get(module, source_name)
        if isinstance(value, (list, tuple)):
            out[key] = _dedupe([v for v in value if isinstance(v, str)])
    return out


def build_plex_tables(reader: SourceReader) -> dict[str, Any]:
    """Extract the Plex search-translation tables that power the preview engine."""
    out: dict[str, Any] = {}
    for name in PLEX_CONSTANTS:
        value = reader.try_get("plex", name)
        if value is not None:
            out[name] = value
    return out


def _default_files_on_disk(source: Path) -> dict[str, dict[str, str]]:
    """Index the default YAML files Kometa actually ships, by kind and name.

    The schema's enum and the shipped files can drift: Kometa 2.4.6 still lists
    ``flixpatrol`` as a valid default, but no ``flixpatrol.yml`` exists any more. Offering
    a default that Kometa will fail to load is worse than omitting it, so the browser
    filters against reality rather than trusting the enum.
    """
    collection_dirs = ["award", "chart", "both", "movie", "show"]
    index: dict[str, dict[str, str]] = {"collections": {}, "overlays": {}, "playlists": {}}

    for folder in collection_dirs:
        for path in sorted((source / "defaults" / folder).glob("*.yml")):
            # Both movie/ and show/ define e.g. decade.yml; either presence is enough.
            index["collections"].setdefault(path.stem, f"{folder}/{path.name}")

    for path in sorted((source / "defaults" / "overlays").glob("*.yml")):
        index["overlays"][path.stem] = f"overlays/{path.name}"

    playlist = source / "defaults" / "playlist.yml"
    if playlist.exists():
        index["playlists"]["playlist"] = "playlist.yml"

    return index


def build_defaults(schema: dict[str, Any], source: Path) -> dict[str, Any]:
    """Read the Defaults catalogue out of the config schema.

    Two shapes appear here. Collection defaults enumerate every name and then bind each
    to a *specific* ``template_variables`` definition through a chain of if/then clauses
    (``oscars`` -> ``award-template-vars``). Overlay and playlist defaults instead point
    every name at one shared definition. We record both so the Defaults browser can
    render the most specific form available for each entry.
    """
    definitions = schema.get("definitions", {})
    on_disk = _default_files_on_disk(source)

    def collect(kind: str, definition_name: str) -> dict[str, Any]:
        node = definitions.get(definition_name, {})
        names = node.get("properties", {}).get("default", {}).get("enum", [])
        shared = node.get("properties", {}).get("template_variables", {}).get("$ref")
        bindings = _walk_if_chain(node.get("allOf", []))
        files = on_disk.get(kind, {})
        available = [name for name in names if name in files]
        return {
            # Only defaults that both the schema allows and Kometa actually ships.
            "names": available,
            "files": {name: files[name] for name in available},
            # Listed by the schema but no longer shipped -- kept for diagnostics so an
            # existing config referencing one can be explained rather than silently ignored.
            "declared_but_missing": [name for name in names if name not in files],
            # Per-default definition, where the schema distinguishes them.
            "template_variable_refs": {k: v for k, v in bindings.items() if k in files},
            # Fallback definition used when a default has no specific binding.
            "shared_template_variable_ref": shared.rsplit("/", 1)[-1] if shared else None,
            "extra_properties": [
                key
                for key in node.get("properties", {})
                if key not in ("default", "template_variables")
            ],
        }

    return {
        "collections": collect("collections", "kometa-default-collection-path"),
        "overlays": collect("overlays", "kometa-default-overlay-path"),
        "playlists": collect("playlists", "kometa-default-playlist-path"),
    }


def _walk_if_chain(all_of: list[dict[str, Any]]) -> dict[str, str]:
    """Flatten a nested if/then/else chain into ``{default_name: definition_name}``.

    The schema nests one clause inside the previous clause's ``else``, so this walks
    down the chain rather than iterating a flat list.
    """
    bindings: dict[str, str] = {}

    def visit(clause: dict[str, Any]) -> None:
        condition = clause.get("if", {})
        const = condition.get("properties", {}).get("default", {}).get("const")
        then = clause.get("then", {})
        ref = then.get("properties", {}).get("template_variables", {}).get("$ref")
        if const and ref:
            bindings[const] = ref.rsplit("/", 1)[-1]
        nested = clause.get("else")
        if isinstance(nested, dict):
            visit(nested)

    for entry in all_of:
        if isinstance(entry, dict):
            visit(entry)
    return bindings


def build_builder_examples(source: Path, builders: set[str]) -> dict[str, dict[str, Any]]:
    """Lift worked examples for each builder out of Kometa's own example files.

    ``json-schema/builders/*.yml`` are annotated galleries the Kometa team maintains --
    real collections using each builder, with banner comments explaining what it does.
    They answer the question a generated form cannot: a schema can say ``plex_search`` is
    an object, but only an example shows that it takes ``all``/``any`` blocks with
    ``sort_by`` and ``limit``.

    Snippets are sliced out of the source text rather than parsed and re-emitted, so the
    formatting stays exactly as written -- and the generator keeps needing nothing but the
    standard library.
    """
    builders_dir = source / "json-schema" / "builders"
    if not builders_dir.is_dir():
        return {}

    # Banner comments look like:  # plex_search — filter-block builder (all/any + ...)
    banner = re.compile(r"^\s*#\s*([a-z][a-z0-9_]*)\s*(?:—|--|-)\s*(\S.*?)\s*$")
    key_line = re.compile(r"^(\s*)([a-z][a-z0-9_]*):(.*)$")

    out: dict[str, dict[str, Any]] = {}

    for path in sorted(builders_dir.glob("*.yml")):
        lines = path.read_text(encoding="utf-8").splitlines()

        for index, line in enumerate(lines):
            match = banner.match(line)
            if match and match.group(1) in builders:
                entry = out.setdefault(match.group(1), {"hint": "", "examples": []})
                if not entry["hint"]:
                    entry["hint"] = match.group(2)
                continue

            match = key_line.match(line)
            if not match:
                continue
            indent, name, inline = match.group(1), match.group(2), match.group(3)
            if name not in builders:
                continue

            entry = out.setdefault(name, {"hint": "", "examples": []})
            snippet = [line]
            if not inline.strip():
                # Block value: take the following more-indented lines.
                for following in lines[index + 1 :]:
                    if not following.strip():
                        snippet.append(following)
                        continue
                    if len(following) - len(following.lstrip(" ")) <= len(indent):
                        break
                    snippet.append(following)

            while snippet and not snippet[-1].strip():
                snippet.pop()

            text = "\n".join(entry_line[len(indent) :] for entry_line in snippet)
            if text not in entry["examples"]:
                entry["examples"].append(text)

    # Lead with the fullest example. Several files use the same builder minimally in
    # passing, and `plex_search: {all: {genre: Action}}` teaches far less than the one
    # showing `all`/`any`, `sort_by` and `limit` together.
    for entry in out.values():
        entry["examples"].sort(key=lambda text: -len(text.splitlines()))
        del entry["examples"][4:]

    return {name: value for name, value in out.items() if value["examples"] or value["hint"]}


def build_schema_property_index(schema: dict[str, Any], definition: str) -> dict[str, str]:
    """Map each property of a definition to its description, for form help text."""
    node = schema.get("definitions", {}).get(definition, {})
    return {
        name: (body.get("description") or "").strip()
        for name, body in node.get("properties", {}).items()
        if isinstance(body, dict)
    }


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------

SCHEMA_FILES = [
    "config-schema.json",
    "collection-schema.json",
    "overlay-schema.json",
    "metadata-schema.json",
    "playlist-schema.json",
    "template-schema.json",
]


def vendor_schemas(source: Path, dest: Path) -> list[str]:
    """Copy Kometa's JSON schemas into the backend's asset directory."""
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in SCHEMA_FILES:
        src = source / "json-schema" / name
        if not src.exists():
            print(f"  ! missing schema: {src}", file=sys.stderr)
            continue
        shutil.copyfile(src, dest / name)
        copied.append(name)
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--kometa-source",
        type=Path,
        default=Path(r"C:\Projects\KometaSource"),
        help="Path to a Kometa checkout",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "backend" / "kometa_assets",
        help="Directory to write catalog.json and vendored schemas into",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if the generated catalog differs from the committed one",
    )
    args = parser.parse_args()

    source: Path = args.kometa_source.resolve()
    if not (source / "modules").is_dir():
        print(f"error: {source} does not look like a Kometa checkout", file=sys.stderr)
        return 2

    version_file = source / "VERSION"
    version = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else "unknown"
    print(f"Reading Kometa {version} from {source}")

    reader = SourceReader(root=source)

    config_schema = json.loads((source / "json-schema" / "config-schema.json").read_text(encoding="utf-8"))
    collection_schema = json.loads((source / "json-schema" / "collection-schema.json").read_text(encoding="utf-8"))

    services = build_builders_by_service(reader)
    builder_groups = build_constant_group(reader, "builder", BUILDER_CONSTANTS)
    detail_groups = build_constant_group(reader, "builder", DETAIL_CONSTANTS)
    plex_tables = build_plex_tables(reader)
    defaults = build_defaults(config_schema, source)

    collection_props = build_schema_property_index(collection_schema, "collection-definition")
    builder_examples = build_builder_examples(source, set(builder_groups.get("all", [])))

    # Every builder the schema knows about should be attributed to a service; anything
    # left over is surfaced so the grouping stays honest as Kometa evolves.
    known = {b for svc in services.values() for b in svc["builders"]}
    declared = set(builder_groups.get("all", []))

    # Kometa's JSON schema is acknowledged to be incomplete (see json-schema/README.md
    # "Known Limitations"). Builders that exist in the Python but not the schema are
    # perfectly valid in a config file, so record them: validation must not reject a
    # builder just because the schema forgot it.
    schema_props = set(collection_schema.get("definitions", {}).get("collection-definition", {}).get("properties", {}))
    missing_from_schema = sorted(declared - schema_props)

    catalog = {
        "kometa_version": version,
        "generated_from": str(source),
        "services": services,
        "builder_groups": builder_groups,
        "detail_groups": detail_groups,
        "plex": plex_tables,
        "defaults": defaults,
        "collection_property_descriptions": collection_props,
        # Worked examples and one-line hints, keyed by builder. This is what the UI shows
        # when the schema alone cannot convey the shape of a value.
        "builder_examples": builder_examples,
        # Consumed by the validator to suppress false "additional property" errors.
        "builders_missing_from_schema": missing_from_schema,
        "diagnostics": {
            "service_builder_count": len(known),
            "all_builders_count": len(declared),
            "builders_missing_from_services": sorted(declared - known),
            "builders_not_in_all_builders": sorted(known - declared),
            "schema_gap_count": len(missing_from_schema),
            "unresolved_constants": reader.unresolved,
        },
    }

    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = out_dir / "catalog.json"
    rendered = json.dumps(catalog, indent=2, sort_keys=False, ensure_ascii=False) + "\n"

    if args.check:
        if not catalog_path.exists():
            print("error: catalog.json does not exist; run without --check first", file=sys.stderr)
            return 1
        if catalog_path.read_text(encoding="utf-8") != rendered:
            print("error: catalog.json is stale; re-run tools/generate_catalog.py", file=sys.stderr)
            return 1
        print("catalog.json is up to date")
        return 0

    catalog_path.write_text(rendered, encoding="utf-8")
    copied = vendor_schemas(source, out_dir / "schemas")

    print(f"  services         {len(services)}")
    print(f"  builders         {len(known)} grouped / {len(declared)} declared")
    print(f"  detail groups    {len(detail_groups)}")
    print(f"  plex tables      {len(plex_tables)}")
    print(f"  defaults         {len(defaults['collections']['names'])} collection, "
          f"{len(defaults['overlays']['names'])} overlay")
    print(f"  builder examples {len(builder_examples)} builders documented")
    print(f"  schemas vendored {len(copied)}")
    if reader.unresolved:
        print(f"  ! {len(reader.unresolved)} unresolved constant(s):", file=sys.stderr)
        for item in reader.unresolved:
            print(f"      {item}", file=sys.stderr)
    print(f"Wrote {catalog_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
