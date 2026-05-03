#!/usr/bin/env python3
"""Extract config-related change events from TiDB release notes."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import subprocess
from typing import Any

from _common import load_json
from _common import metadata_relpath
from _common import normalize_version
from _common import write_json


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_DOCS_REPO = ROOT.parent / "docs"

CONTENT_TYPES = {
    "system_variables": {
        "component": "tidb",
        "known_path": "normalized/system_variables.json",
        "known_key": "VARIABLE_NAME",
    },
    "tidb_config": {
        "component": "tidb",
        "known_path": "normalized/show_config_tidb.json",
        "known_key": "Name",
    },
    "tikv_config": {
        "component": "tikv",
        "known_path": "normalized/show_config_tikv.json",
        "known_key": "Name",
    },
    "pd_config": {
        "component": "pd",
        "known_path": "normalized/show_config_pd.json",
        "known_key": "Name",
    },
    "tiflash_config": {
        "component": "tiflash",
        "known_path": "normalized/show_config_tiflash.json",
        "known_key": "Name",
    },
}

DOC_PATH_CONTENT_TYPES = [
    ("system-variables", "system_variables"),
    ("tidb-configuration-file", "tidb_config"),
    ("tikv-configuration-file", "tikv_config"),
    ("pd-configuration-file", "pd_config"),
    ("tiflash-configuration", "tiflash_config"),
]

COMPONENT_CONTENT_TYPES = {
    "tidb": "tidb_config",
    "tikv": "tikv_config",
    "pd": "pd_config",
    "tiflash": "tiflash_config",
}

HEADING_RE = re.compile(r"^(#{2,6})\s+(.+?)\s*$")
BULLET_RE = re.compile(r"^(\s*)[-+*]\s+(.+?)\s*$")
CODE_LINK_RE = re.compile(r"\[`([^`]+)`\]\(([^)]+)\)")
CODE_RE = re.compile(r"`([^`]+)`")
RELEASE_FILE_RE = re.compile(r"release-(\d+)\.(\d+)\.(\d+)\.md$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(ROOT), help="Config data repository root")
    parser.add_argument("--docs-repo", default=str(DEFAULT_DOCS_REPO), help="Local pingcap/docs repository")
    parser.add_argument(
        "--release-notes-ref",
        help="Docs git ref that contains the release notes. Defaults to upstream/release-<max scope minor>",
    )
    parser.add_argument("--scope", default=str(ROOT / "mvp-versions.json"), help="Version scope JSON")
    parser.add_argument("--output", default=str(ROOT / "metadata" / "release-note-events.json"))
    return parser.parse_args()


def version_tuple(version: str | None) -> tuple[int, int, int] | None:
    if not version:
        return None
    match = re.match(r"^v?(\d+)\.(\d+)(?:\.(\d+))?", version)
    if not match:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch or 0)


def version_minor(version: str) -> tuple[int, int] | None:
    parsed = version_tuple(version)
    return parsed[:2] if parsed else None


def versions_from_scope(scope_path: pathlib.Path) -> list[str]:
    payload = load_json(scope_path)
    return [normalize_version(item["version"]) for item in payload.get("versions", [])]


def default_release_notes_ref(scope_versions: list[str]) -> str:
    parsed_versions = [version_tuple(version) for version in scope_versions]
    parsed_versions = [version for version in parsed_versions if version]
    if not parsed_versions:
        return "upstream/master"
    major, minor, _ = max(parsed_versions)
    return f"upstream/release-{major}.{minor}"


def release_note_url(version: str) -> str:
    parsed = version_tuple(version)
    if not parsed:
        return ""
    major, minor, patch = parsed
    return f"https://docs.pingcap.com/tidb/v{major}.{minor}/release-{major}.{minor}.{patch}"


def run_git(repo: pathlib.Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def git_commit(repo: pathlib.Path, ref: str) -> str:
    return run_git(repo, ["rev-parse", ref]).strip()


def load_known_keys(repo_root: pathlib.Path) -> dict[str, set[str]]:
    known: dict[str, set[str]] = {content_type: set() for content_type in CONTENT_TYPES}
    for capture_dir in sorted(repo_root.glob("v*")):
        if not capture_dir.is_dir():
            continue
        for content_type, source in CONTENT_TYPES.items():
            path = capture_dir / source["known_path"]
            if not path.exists():
                continue
            for row in load_json(path):
                key = row.get(source["known_key"])
                if key:
                    known[content_type].add(str(key))
    return known


def infer_content_type(url: str | None, component: str | None = None) -> str | None:
    lowered_url = (url or "").lower()
    for marker, content_type in DOC_PATH_CONTENT_TYPES:
        if marker in lowered_url:
            return content_type
    if component:
        return COMPONENT_CONTENT_TYPES.get(component.lower())
    return None


def normalize_item_key(raw_key: str, content_type: str, known: dict[str, set[str]]) -> str | None:
    raw_key = raw_key.strip()
    if not raw_key:
        return None

    keys = known.get(content_type, set())
    if raw_key in keys:
        return raw_key

    lower_matches = [key for key in keys if key.lower() == raw_key.lower()]
    if len(lower_matches) == 1:
        return lower_matches[0]

    suffix_matches = [key for key in keys if key.endswith(f".{raw_key}")]
    if len(suffix_matches) == 1:
        return suffix_matches[0]

    if "." in raw_key:
        tail = raw_key.split(".")[-1]
        tail_matches = [key for key in keys if key == tail or key.endswith(f".{tail}")]
        if len(tail_matches) == 1:
            return tail_matches[0]

    return None


def split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def is_table_separator(line: str) -> bool:
    cells = split_table_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def strip_markdown(text: str) -> str:
    text = re.sub(r"\[`([^`]+)`\]\([^)]+\)", r"`\1`", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"</li>\s*<li>", "; ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"\s+#\d+", "", text)
    text = re.sub(r"\s+@\w[\w-]*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -")


def clean_note(text: str) -> str:
    text = re.sub(r"^\s*[-+*]\s+", "", text.strip())
    text = strip_markdown(text)
    text = re.sub(r"\s+For details, see\.?$", ".", text)
    return text


def extract_replacement(text: str) -> str | None:
    patterns = [
        r"(?:replaced by|superseded by)\s+(?:the\s+)?(?:\[`([^`]+)`\]|\`([^`]+)`)",
        r"replaces\s+.*?\s+with\s+(?:the\s+new\s+)?(?:\[`([^`]+)`\]|\`([^`]+)`)",
        r"(?:instead,\s+)?use\s+(?:the\s+\w+\s+)?(?:\[`([^`]+)`\]|\`([^`]+)`)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return next((group for group in match.groups() if group), None)
    return None


def actual_deprecation(text: str) -> bool:
    lowered = text.lower()
    explicit_current = bool(re.search(r"\b(?:is|are|has been|have been)\s+deprecated\b", lowered))
    if ("will be deprecated" in lowered or "planned for deprecation" in lowered) and not explicit_current:
        return False
    return bool(
        "deprecates" in lowered
        or explicit_current
        or re.search(r"\bdeprecated\s+in\s+v\d+\.\d+", lowered)
        or re.search(r"starting from\s+v\d+\.\d+.*\bdeprecated\b", lowered)
    )


def event_type_from_text(change_type: str, note: str, section_kind: str) -> str | None:
    lowered_change = change_type.lower()
    lowered_note = note.lower()
    lowered = f"{lowered_change} {lowered_note}"

    if "newly added" in lowered_change or lowered_change == "new":
        return None
    if "deprecated" in lowered_change:
        return "deprecated"
    if section_kind == "deprecated":
        return "deprecated" if actual_deprecation(note) else None
    if actual_deprecation(note):
        return "deprecated"
    if section_kind == "removed" or "removed" in lowered_change or "deleted" in lowered_change:
        return "removed"
    if re.search(r"\b(has been\s+)?removed\b|\bdeleted\b", lowered_note):
        return "removed"
    if "modified" in lowered_change or "changed" in lowered_change:
        return "modified"
    if re.search(r"\bchange[sd]?\b|\bdefault value\b|\bscope\b|\bstarting from\s+v\d+\.\d+", lowered_note):
        return "modified"
    return None


def extract_refs(
    text: str,
    *,
    default_content_type: str | None,
    component: str | None,
    known: dict[str, set[str]],
) -> list[tuple[str, str, str]]:
    refs: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for match in CODE_LINK_RE.finditer(text):
        raw_key, url = match.groups()
        content_type = infer_content_type(url, component) or default_content_type
        if not content_type:
            continue
        item_key = normalize_item_key(raw_key, content_type, known)
        if not item_key:
            continue
        component_name = CONTENT_TYPES[content_type]["component"]
        key = (content_type, component_name, item_key)
        if key not in seen:
            seen.add(key)
            refs.append(key)

    for match in CODE_RE.finditer(CODE_LINK_RE.sub("", text)):
        raw_key = match.group(1)
        content_type = default_content_type
        if not content_type:
            continue
        item_key = normalize_item_key(raw_key, content_type, known)
        if not item_key:
            continue
        component_name = CONTENT_TYPES[content_type]["component"]
        key = (content_type, component_name, item_key)
        if key not in seen:
            seen.add(key)
            refs.append(key)

    return refs


def create_event(
    *,
    version: str,
    release_file: str,
    line_start: int,
    line_end: int,
    content_type: str,
    component: str,
    item_key: str,
    event_type: str,
    change_type: str,
    note: str,
    replacement: str | None,
) -> dict[str, Any]:
    return {
        "version": version,
        "content_type": content_type,
        "component": component,
        "item_key": item_key,
        "display_name": item_key,
        "event_type": event_type,
        "change_type": change_type,
        "change_note": clean_note(note),
        "replacement": replacement,
        "release_note_file": release_file,
        "release_note_line_start": line_start,
        "release_note_line_end": line_end,
        "release_note_url": release_note_url(version),
        "source": "release_notes",
    }


def parse_table(
    *,
    lines: list[str],
    start: int,
    version: str,
    release_file: str,
    section_kind: str,
    known: dict[str, set[str]],
) -> tuple[list[dict[str, Any]], int]:
    headers = [strip_markdown(cell).lower() for cell in split_table_row(lines[start])]
    events: list[dict[str, Any]] = []
    i = start + 2

    def column_index(markers: list[str]) -> int | None:
        for marker in markers:
            for idx, header in enumerate(headers):
                if marker in header:
                    return idx
        return None

    item_idx = column_index(["variable", "configuration parameter", "configuration item", "parameter"])
    change_idx = column_index(["change type"])
    desc_idx = column_index(["description"])
    component_idx = column_index(["component", "configuration file"])

    if item_idx is None or change_idx is None or desc_idx is None:
        return events, i

    while i < len(lines) and lines[i].strip().startswith("|"):
        if is_table_separator(lines[i]):
            i += 1
            continue
        cells = split_table_row(lines[i])
        if len(cells) < len(headers):
            i += 1
            continue

        item_cell = cells[item_idx]
        change_type = strip_markdown(cells[change_idx])
        description = cells[desc_idx]
        component = strip_markdown(cells[component_idx]) if component_idx is not None and component_idx < len(cells) else None
        default_content_type = infer_content_type(None, component)
        event_type = event_type_from_text(change_type, description, section_kind)
        if not event_type:
            i += 1
            continue

        refs = extract_refs(
            item_cell,
            default_content_type=default_content_type,
            component=component,
            known=known,
        )
        for content_type, component_name, item_key in refs:
            events.append(
                create_event(
                    version=version,
                    release_file=release_file,
                    line_start=i + 1,
                    line_end=i + 1,
                    content_type=content_type,
                    component=component_name,
                    item_key=item_key,
                    event_type=event_type,
                    change_type=change_type,
                    note=description,
                    replacement=extract_replacement(description),
                )
            )
        i += 1

    return events, i


def parent_bullet_line(lines: list[str], start: int, indent: int) -> str | None:
    for idx in range(start - 1, -1, -1):
        match = BULLET_RE.match(lines[idx])
        if match and len(match.group(1)) < indent:
            return lines[idx]
    return None


def bullet_block(lines: list[str], start: int) -> tuple[int, int, list[str]]:
    match = BULLET_RE.match(lines[start])
    if not match:
        return start, start + 1, [lines[start]]
    indent = len(match.group(1))
    end = start + 1
    while end < len(lines):
        heading = HEADING_RE.match(lines[end])
        bullet = BULLET_RE.match(lines[end])
        if heading:
            break
        if bullet and len(bullet.group(1)) <= indent:
            break
        end += 1
    return indent, end, lines[start:end]


def refs_segment_for_bullet(block_text: str, event_type: str | None) -> str | None:
    lowered = block_text.lower()
    if "new configuration items:" in lowered:
        return None
    if "deprecated configuration items:" in lowered:
        return block_text[lowered.index("deprecated configuration items:") :]
    if event_type == "deprecated" and "for more information" in lowered:
        return block_text[: lowered.index("for more information")]
    if event_type == "deprecated" and "deprecates the following" in lowered and "replaces them with the new" in lowered:
        return None
    return block_text


def parse_bullet(
    *,
    lines: list[str],
    start: int,
    version: str,
    release_file: str,
    section_kind: str,
    known: dict[str, set[str]],
) -> tuple[list[dict[str, Any]], int]:
    indent, end, block_lines = bullet_block(lines, start)
    block_text = "\n".join(block_lines)
    event_type = event_type_from_text("", block_text, section_kind)
    if not event_type:
        return [], end

    note_lines = block_lines
    parent = parent_bullet_line(lines, start, indent)
    if parent and ("deprecates" in parent.lower() or "replaces" in parent.lower()):
        note_lines = [parent, *block_lines]
    note = "\n".join(note_lines)
    refs_segment = refs_segment_for_bullet(block_text, event_type)
    if not refs_segment:
        return [], end

    refs = extract_refs(
        refs_segment,
        default_content_type=None,
        component=None,
        known=known,
    )
    events = [
        create_event(
            version=version,
            release_file=release_file,
            line_start=start + 1,
            line_end=end,
            content_type=content_type,
            component=component,
            item_key=item_key,
            event_type=event_type,
            change_type=event_type.title(),
            note=note,
            replacement=extract_replacement(note),
        )
        for content_type, component, item_key in refs
    ]
    return events, end


def section_kind(title: str) -> str | None:
    lowered = title.lower()
    if lowered == "compatibility changes":
        return "compatibility"
    if lowered == "deprecated features":
        return "deprecated"
    if lowered == "removed features":
        return "removed"
    return None


def relevant_ranges(lines: list[str]) -> list[tuple[int, int, str]]:
    headings: list[tuple[int, int, str]] = []
    for idx, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if match:
            headings.append((idx, len(match.group(1)), match.group(2).strip()))

    ranges: list[tuple[int, int, str]] = []
    for pos, (start, level, title) in enumerate(headings):
        kind = section_kind(title)
        if not kind or level != 2:
            continue
        end = len(lines)
        for next_start, next_level, _ in headings[pos + 1 :]:
            if next_level <= level:
                end = next_start
                break
        ranges.append((start + 1, end, kind))
    return ranges


def parse_release_note(path: str, text: str, known: dict[str, set[str]]) -> list[dict[str, Any]]:
    match = RELEASE_FILE_RE.search(path)
    if not match:
        return []
    version = normalize_version(".".join(match.groups()))
    lines = text.splitlines()
    events: list[dict[str, Any]] = []

    for start, end, kind in relevant_ranges(lines):
        i = start
        while i < end:
            stripped = lines[i].strip()
            if stripped.startswith("|") and i + 1 < end and is_table_separator(lines[i + 1]):
                table_events, next_i = parse_table(
                    lines=lines,
                    start=i,
                    version=version,
                    release_file=path,
                    section_kind=kind,
                    known=known,
                )
                events.extend(table_events)
                i = next_i
                continue
            if BULLET_RE.match(lines[i]):
                bullet_events, _ = parse_bullet(
                    lines=lines,
                    start=i,
                    version=version,
                    release_file=path,
                    section_kind=kind,
                    known=known,
                )
                events.extend(bullet_events)
            i += 1

    return events


def dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for event in events:
        if not event.get("change_note"):
            continue
        key = (
            event["version"],
            event["content_type"],
            event["component"],
            event["item_key"],
            event["event_type"],
        )
        existing = merged.get(key)
        if not existing or len(event["change_note"]) > len(existing.get("change_note", "")):
            merged[key] = event
    return sorted(
        merged.values(),
        key=lambda row: (
            version_tuple(row["version"]) or (0, 0, 0),
            row["content_type"],
            row["item_key"],
            row["event_type"],
        ),
    )


def release_note_paths(docs_repo: pathlib.Path, docs_ref: str, scope_versions: list[str]) -> list[str]:
    parsed_versions = [version_tuple(version) for version in scope_versions]
    parsed_versions = [version for version in parsed_versions if version]
    files = [
        line
        for line in run_git(docs_repo, ["ls-tree", "-r", "--name-only", docs_ref, "releases"]).splitlines()
        if RELEASE_FILE_RE.search(line)
    ]
    if not parsed_versions:
        return sorted(files)
    min_version = min(parsed_versions)
    max_version = max(parsed_versions)
    paths = []
    for path in sorted(files):
        match = RELEASE_FILE_RE.search(path)
        if not match:
            continue
        version = tuple(int(part) for part in match.groups())
        if min_version <= version <= max_version:
            paths.append(path)
    return paths


def release_note_text(docs_repo: pathlib.Path, docs_ref: str, path: str) -> str:
    return run_git(docs_repo, ["show", f"{docs_ref}:{path}"])


def main() -> int:
    args = parse_args()
    repo_root = pathlib.Path(args.repo_root).resolve()
    docs_repo = pathlib.Path(args.docs_repo).resolve()
    scope_path = pathlib.Path(args.scope).resolve()

    scope_versions = versions_from_scope(scope_path)
    docs_ref = args.release_notes_ref or default_release_notes_ref(scope_versions)
    known = load_known_keys(repo_root)
    paths = release_note_paths(docs_repo, docs_ref, scope_versions)
    events: list[dict[str, Any]] = []
    for path in paths:
        events.extend(parse_release_note(path, release_note_text(docs_repo, docs_ref, path), known))
    events = dedupe_events(events)

    output = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": {
            "docs_repo": metadata_relpath(docs_repo, repo_root),
            "docs_ref": docs_ref,
            "docs_commit": git_commit(docs_repo, docs_ref),
            "scope": metadata_relpath(scope_path, repo_root),
            "release_note_files": len(paths),
        },
        "counts": {
            "events": len(events),
            "modified": sum(1 for event in events if event["event_type"] == "modified"),
            "removed": sum(1 for event in events if event["event_type"] == "removed"),
            "deprecated": sum(1 for event in events if event["event_type"] == "deprecated"),
        },
        "events": events,
    }
    write_json(pathlib.Path(args.output), output)
    print(json.dumps({"output": args.output, **output["counts"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
