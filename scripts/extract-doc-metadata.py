#!/usr/bin/env python3
"""Extract config item metadata from the TiDB docs repository."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_DOCS_REPO = ROOT.parent / "docs"

DOC_SOURCES = {
    "system_variables": {
        "component": "tidb",
        "path": "system-variables.md",
        "known_path": "normalized/system_variables.json",
        "known_key": "VARIABLE_NAME",
    },
    "tidb_config": {
        "component": "tidb",
        "path": "tidb-configuration-file.md",
        "known_path": "normalized/show_config_tidb.json",
        "known_key": "Name",
    },
    "tikv_config": {
        "component": "tikv",
        "path": "tikv-configuration-file.md",
        "known_path": "normalized/show_config_tikv.json",
        "known_key": "Name",
    },
    "pd_config": {
        "component": "pd",
        "path": "pd-configuration-file.md",
        "known_path": "normalized/show_config_pd.json",
        "known_key": "Name",
    },
    "tiflash_config": {
        "component": "tiflash",
        "path": "tiflash/tiflash-configuration.md",
        "known_path": "normalized/show_config_tiflash.json",
        "known_key": "Name",
    },
}

LOW_CONFIDENCE_MARKERS = [
    "might be deprecated",
    "will be deprecated in a future release",
    "might be changed or removed",
    "not recommended",
    "experimental feature",
]

HEADING_RE = re.compile(r"^(#{2,6})\s+(.+?)\s*$")
VERSION_RE = re.compile(r"v\d+\.\d+(?:\.\d+)?", re.IGNORECASE)
CODE_RE = re.compile(r"`([^`]+)`")


@dataclass
class Heading:
    line_no: int
    level: int
    title: str
    end_line_no: int
    stack_key: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(ROOT), help="Config data repository root")
    parser.add_argument("--docs-repo", default=str(DEFAULT_DOCS_REPO), help="Local pingcap/docs repository")
    parser.add_argument("--scope", default=str(ROOT / "mvp-versions.json"), help="Version scope JSON")
    parser.add_argument("--remote", default="upstream", help="Docs remote name to read")
    parser.add_argument("--output", default=str(ROOT / "metadata" / "config-item-metadata.json"))
    parser.add_argument("--candidates", default=str(ROOT / "metadata" / "doc-metadata-candidates.json"))
    parser.add_argument("--fetch", action="store_true", help="Fetch docs refs before extracting")
    return parser.parse_args()


def run_git(docs_repo: pathlib.Path, args: list[str], check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(docs_repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        raise SystemExit(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_version(version: str) -> str:
    match = re.match(r"^v?(\d+)\.(\d+)(?:\.(\d+))?", version)
    if not match:
        return version
    major, minor, patch = match.groups()
    return f"v{int(major)}.{int(minor)}.{int(patch or 0)}"


def version_tuple(version: str | None) -> tuple[int, int, int] | None:
    if not version:
        return None
    match = re.match(r"^v?(\d+)\.(\d+)(?:\.(\d+))?", version)
    if not match:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch or 0)


def release_branch(version: str, remote: str) -> str:
    parsed = version_tuple(version)
    if not parsed:
        raise ValueError(f"invalid version: {version}")
    major, minor, _ = parsed
    return f"{remote}/release-{major}.{minor}"


def docs_version_from_branch(branch: str) -> str:
    match = re.search(r"release-(\d+\.\d+)$", branch)
    return f"v{match.group(1)}" if match else branch


def slugify(title: str) -> str:
    title = re.sub(r"<span[^>]*>(.*?)</span>", r" \1", title)
    title = re.sub(r"<[^>]+>", "", title)
    title = title.replace("`", "")
    title = re.sub(r"\((?:D|d)eprecated\)", "", title)
    title = title.lower()
    title = re.sub(r"[^a-z0-9 _.-]+", "", title)
    title = title.replace(".", "")
    title = re.sub(r"\s+", "-", title.strip())
    return title


def clean_heading_title(title: str) -> str:
    title = re.sub(r"<span[^>]*>.*?</span>", "", title)
    title = re.sub(r"<[^>]+>", "", title)
    title = re.sub(r"\((?:D|d)eprecated\)", "", title)
    title = title.replace("`", "")
    return title.strip()


def heading_segment(title: str, content_type: str) -> str | None:
    code = CODE_RE.search(title)
    if code:
        return code.group(1).strip()

    cleaned = clean_heading_title(title)
    if content_type == "system_variables":
        first = cleaned.split()[0] if cleaned.split() else ""
        return first if re.fullmatch(r"[A-Za-z0-9_]+", first) else None

    if re.fullmatch(r"[A-Za-z0-9_.-]+", cleaned):
        return cleaned
    return None


def parse_headings(lines: list[str], content_type: str) -> list[Heading]:
    raw: list[tuple[int, int, str]] = []
    for idx, line in enumerate(lines, start=1):
        match = HEADING_RE.match(line)
        if match:
            raw.append((idx, len(match.group(1)), match.group(2)))

    headings: list[Heading] = []
    stack: list[tuple[int, str | None]] = []
    for pos, (line_no, level, title) in enumerate(raw):
        while stack and stack[-1][0] >= level:
            stack.pop()
        segment = heading_segment(title, content_type)
        key = None
        if segment:
            key_segments = [item for _, item in stack if item] + [segment]
            key = ".".join(key_segments)
        end = len(lines) + 1
        for next_line_no, next_level, _ in raw[pos + 1 :]:
            if next_level <= level:
                end = next_line_no
                break
        headings.append(Heading(line_no, level, title, end, key))
        stack.append((level, segment))
    return headings


def load_known_keys(repo_root: pathlib.Path) -> dict[str, set[str]]:
    known: dict[str, set[str]] = {content_type: set() for content_type in DOC_SOURCES}
    for capture_dir in sorted(repo_root.glob("v*")):
        if not capture_dir.is_dir():
            continue
        for content_type, source in DOC_SOURCES.items():
            path = capture_dir / source["known_path"]
            if not path.exists():
                continue
            for row in load_json(path):
                key = row.get(source["known_key"])
                if key:
                    known[content_type].add(str(key))
    return known


def choose_item_key(raw_key: str | None, content_type: str, known: dict[str, set[str]]) -> tuple[str | None, bool]:
    if not raw_key:
        return None, False
    segments = raw_key.split(".")
    variants = [raw_key, segments[-1]]
    if content_type == "tiflash_config":
        variants.extend([f"engine-store.{raw_key}", f"raftstore-proxy.{raw_key}"])
    for variant in variants:
        if variant in known.get(content_type, set()):
            return variant, True
    return raw_key, False


def branch_file(docs_repo: pathlib.Path, branch: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(docs_repo), "show", f"{branch}:{path}"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def branch_commit(docs_repo: pathlib.Path, branch: str) -> str:
    return run_git(docs_repo, ["rev-parse", branch]).strip()


def extract_versions(text: str) -> list[str]:
    seen = []
    for match in VERSION_RE.finditer(text):
        version = normalize_version(match.group(0))
        if version not in seen:
            seen.append(version)
    return seen


def extract_new_since(title: str) -> str | None:
    match = re.search(r"New in\s+(v\d+\.\d+(?:\.\d+)?)", title, flags=re.IGNORECASE)
    return normalize_version(match.group(1)) if match else None


def sentences_with_markers(text: str) -> list[str]:
    collapsed = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?])\s+", collapsed)
    matches = []
    for part in parts:
        lowered = part.lower()
        if any(marker in lowered for marker in ["deprecated", "deprecates", "removed", "superseded", "replaced by", "not recommended", "experimental feature"]):
            matches.append(part.strip())
    return matches


def extract_replacement(text: str) -> str | None:
    patterns = [
        r"(?:replaced by|superseded by)\s+(?:the\s+\w+\s+)?(?:\[`([^`]+)`\]|\`([^`]+)`)",
        r"(?:instead,\s+)?use\s+(?:the\s+\w+\s+)?(?:\[`([^`]+)`\]|\`([^`]+)`)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return next(group for group in match.groups() if group)
    return None


def analyze_lifecycle(title: str, block_text: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    lowered = block_text.lower()
    title_deprecated = bool(re.search(r"\((?:D|d)eprecated\)", title))
    lifecycle: dict[str, Any] = {
        "deprecated": False,
        "deprecated_since": None,
        "deprecated_since_versions": [],
        "removed_since": None,
        "replacement": None,
        "confidence": 0.0,
        "raw_note": None,
    }
    candidates: list[dict[str, Any]] = []

    low_confidence = any(marker in lowered for marker in LOW_CONFIDENCE_MARKERS)

    if title_deprecated:
        lifecycle["deprecated"] = True
        lifecycle["confidence"] = max(lifecycle["confidence"], 0.85)
        lifecycle["raw_note"] = clean_heading_title(title)
        candidates.append(
            {
                "kind": "deprecated",
                "confidence": 0.85,
                "versions": [],
                "text": title,
            }
        )

    for sentence in sentences_with_markers(block_text):
        sentence_lower = sentence.lower()
        versions = extract_versions(sentence)
        candidate_confidence = 0.45
        candidate_kind = "review"
        if "deprecated" in sentence_lower or "deprecates" in sentence_lower:
            candidate_kind = "deprecated"
            candidate_confidence = 0.75
            if re.search(r"(starting from|since|from|deprecated since|deprecated in)\s+v\d+\.\d+", sentence_lower):
                candidate_confidence = 0.95
                lifecycle["deprecated"] = True
                if len(versions) == 1:
                    lifecycle["deprecated_since"] = versions[0]
                elif len(versions) > 1:
                    lifecycle["deprecated_since_versions"] = versions
                lifecycle["confidence"] = max(lifecycle["confidence"], candidate_confidence)
                lifecycle["raw_note"] = sentence
            elif title_deprecated and not low_confidence:
                lifecycle["deprecated"] = True
                lifecycle["confidence"] = max(lifecycle["confidence"], 0.85)
                lifecycle["raw_note"] = lifecycle["raw_note"] or sentence
        if "removed" in sentence_lower and versions and "future release" not in sentence_lower and "will be removed" not in sentence_lower:
            candidate_kind = "removed"
            candidate_confidence = max(candidate_confidence, 0.65)
            if re.search(r"(starting from|since|from)\s+v\d+\.\d+.*\b(this configuration item|this variable|configuration item|variable)\b.*\b(removed|has been removed|is removed)\b", sentence_lower):
                lifecycle["removed_since"] = versions[0]
                lifecycle["confidence"] = max(lifecycle["confidence"], 0.8)
                lifecycle["raw_note"] = lifecycle["raw_note"] or sentence
        if "not recommended" in sentence_lower or "experimental feature" in sentence_lower:
            candidate_confidence = min(candidate_confidence, 0.45)
        candidates.append(
            {
                "kind": candidate_kind,
                "confidence": candidate_confidence,
                "versions": versions,
                "text": sentence,
            }
        )

    replacement = extract_replacement(block_text)
    if replacement:
        lifecycle["replacement"] = replacement

    return lifecycle, candidates


def strip_markdown(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^>\s*", "", line)
    line = re.sub(r"^[-+*]\s+", "", line)
    line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
    line = line.replace("`", "")
    return line.strip()


def extract_description(lines: list[str]) -> str | None:
    skip_prefixes = (
        "scope:",
        "persists to cluster:",
        "applies to hint",
        "type:",
        "default value:",
        "possible values:",
        "range:",
        "minimum value:",
        "maximum value:",
        "unit:",
        "warning:",
        "note:",
        "tip:",
    )
    in_code = False
    for raw in lines:
        stripped_raw = raw.strip()
        if stripped_raw.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not stripped_raw or stripped_raw.startswith("#"):
            continue
        text = strip_markdown(raw)
        if not text or text.lower().startswith(skip_prefixes):
            continue
        lowered = text.lower()
        if any(marker in lowered for marker in ["deprecated", "experimental feature", "not recommended", "removed without prior notice"]):
            continue
        return text[:800]
    return None


def extract_system_variable_fields(lines: list[str]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for raw in lines:
        text = strip_markdown(raw)
        lowered = text.lower()
        if lowered.startswith("scope:"):
            fields["variable_scope"] = text.split(":", 1)[1].strip()
        elif lowered.startswith("persists to cluster:"):
            fields["persists_to_cluster"] = text.split(":", 1)[1].strip()
        elif lowered.startswith("applies to hint"):
            fields["applies_to_set_var"] = text.rsplit(":", 1)[-1].strip()
        elif lowered.startswith("type:"):
            fields["value_type"] = text.split(":", 1)[1].strip().lower()
    return fields


def merge_item(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    existing_docs_version = version_tuple((merged.get("metadata") or {}).get("docs_version"))
    incoming_docs_version = version_tuple((incoming.get("metadata") or {}).get("docs_version"))
    incoming_is_newer = bool(incoming_docs_version and (not existing_docs_version or incoming_docs_version >= existing_docs_version))
    for key, value in incoming.items():
        if value in (None, "", [], {}):
            continue
        if key == "metadata":
            metadata = dict(merged.get("metadata") or {})
            for meta_key, meta_value in value.items():
                if meta_key == "doc_sources":
                    sources = metadata.setdefault("doc_sources", [])
                    for source in meta_value:
                        if source not in sources:
                            sources.append(source)
                elif meta_key == "deprecated_since_versions":
                    versions = metadata.setdefault("deprecated_since_versions", [])
                    for version in meta_value:
                        if version not in versions:
                            versions.append(version)
                else:
                    metadata[meta_key] = meta_value
            merged[key] = metadata
        elif key == "deprecated_since":
            old_tuple = version_tuple(merged.get(key))
            new_tuple = version_tuple(value)
            if not old_tuple or (new_tuple and new_tuple < old_tuple):
                merged[key] = value
        elif key == "confidence":
            merged[key] = max(float(merged.get(key) or 0), float(value or 0))
        elif key in {"description", "docs_url", "value_type", "variable_scope", "persists_to_cluster", "applies_to_set_var", "replacement"}:
            if incoming_is_newer or not merged.get(key):
                merged[key] = value
        else:
            merged.setdefault(key, value)
    return merged


def process_doc(
    *,
    docs_repo: pathlib.Path,
    repo_root: pathlib.Path,
    branch: str,
    branch_commit_hash: str,
    content_type: str,
    source: dict[str, str],
    known: dict[str, set[str]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    text = branch_file(docs_repo, branch, source["path"])
    if text is None:
        return {}, []

    lines = text.splitlines()
    headings = parse_headings(lines, content_type)
    docs_version = docs_version_from_branch(branch)
    items: dict[str, dict[str, Any]] = {}
    candidates: list[dict[str, Any]] = []

    for heading in headings:
        item_key, known_match = choose_item_key(heading.stack_key, content_type, known)
        if not item_key:
            continue
        block_lines = lines[heading.line_no : heading.end_line_no - 1]
        block_text = "\n".join(block_lines)
        lifecycle, lifecycle_candidates = analyze_lifecycle(heading.title, block_text)
        new_since = extract_new_since(heading.title)
        docs_path = source["path"].removesuffix(".md")
        docs_url = f"https://docs.pingcap.com/tidb/{docs_version}/{docs_path}#{slugify(heading.title)}"
        source_info = {
            "branch": branch,
            "commit": branch_commit_hash,
            "file": source["path"],
            "line_start": heading.line_no,
            "line_end": heading.end_line_no - 1,
        }

        metadata = {
            "docs_branch": branch,
            "docs_commit": branch_commit_hash,
            "docs_version": docs_version,
            "doc_sources": [source_info],
        }
        if lifecycle.get("deprecated_since_versions"):
            metadata["deprecated_since_versions"] = lifecycle["deprecated_since_versions"]

        item = {
            "content_type": content_type,
            "component": source["component"],
            "item_key": item_key,
            "display_name": item_key,
            "description": extract_description(block_lines),
            "docs_url": docs_url,
            "new_since": new_since,
            "deprecated_since": lifecycle.get("deprecated_since"),
            "removed_since": lifecycle.get("removed_since"),
            "replacement": lifecycle.get("replacement"),
            "source": "docs",
            "confidence": lifecycle.get("confidence") or (0.7 if known_match else 0.4),
            "metadata": metadata,
        }
        if content_type == "system_variables":
            item.update(extract_system_variable_fields(block_lines))

        if known_match:
            key = f"{content_type}\0{source['component']}\0{item_key}"
            items[key] = merge_item(items.get(key, {}), item)
            if content_type == "tikv_config":
                tiflash_key = f"raftstore-proxy.{item_key}"
                if tiflash_key in known.get("tiflash_config", set()):
                    tiflash_item = dict(item)
                    tiflash_item["content_type"] = "tiflash_config"
                    tiflash_item["component"] = "tiflash"
                    tiflash_item["item_key"] = tiflash_key
                    tiflash_item["display_name"] = tiflash_key
                    tiflash_metadata = dict(tiflash_item.get("metadata") or {})
                    tiflash_metadata["derived_from"] = {
                        "content_type": "tikv_config",
                        "component": "tikv",
                        "item_key": item_key,
                        "reason": "TiFlash exposes TiKV proxy configuration under the raftstore-proxy prefix.",
                    }
                    tiflash_item["metadata"] = tiflash_metadata
                    tiflash_row_key = f"tiflash_config\0tiflash\0{tiflash_key}"
                    items[tiflash_row_key] = merge_item(items.get(tiflash_row_key, {}), tiflash_item)

        for candidate in lifecycle_candidates:
            candidates.append(
                {
                    "content_type": content_type,
                    "component": source["component"],
                    "item_key": item_key,
                    "matched_known_item": known_match,
                    "source_branch": branch,
                    "source_commit": branch_commit_hash,
                    "source_file": source["path"],
                    "source_line_start": heading.line_no,
                    "source_line_end": heading.end_line_no - 1,
                    "docs_url": docs_url,
                    "review_status": "needs_review" if candidate["confidence"] < 0.8 or not known_match else "auto_applied",
                    **candidate,
                }
            )

    return items, candidates


def versions_from_scope(scope_path: pathlib.Path) -> list[str]:
    payload = load_json(scope_path)
    return [normalize_version(item["version"]) for item in payload.get("versions", [])]


def main() -> int:
    args = parse_args()
    repo_root = pathlib.Path(args.repo_root).resolve()
    docs_repo = pathlib.Path(args.docs_repo).resolve()
    scope_path = pathlib.Path(args.scope).resolve()

    if args.fetch:
        run_git(docs_repo, ["fetch", args.remote, "--prune"])

    versions = versions_from_scope(scope_path)
    branches = []
    for version in versions:
        branch = release_branch(version, args.remote)
        if branch not in branches:
            branches.append(branch)

    known = load_known_keys(repo_root)
    branch_commits = {branch: branch_commit(docs_repo, branch) for branch in branches}

    merged_items: dict[str, dict[str, Any]] = {}
    candidates: list[dict[str, Any]] = []
    for branch in branches:
        for content_type, source in DOC_SOURCES.items():
            items, doc_candidates = process_doc(
                docs_repo=docs_repo,
                repo_root=repo_root,
                branch=branch,
                branch_commit_hash=branch_commits[branch],
                content_type=content_type,
                source=source,
                known=known,
            )
            for key, item in items.items():
                merged_items[key] = merge_item(merged_items.get(key, {}), item)
            candidates.extend(doc_candidates)

    items = sorted(merged_items.values(), key=lambda row: (row["content_type"], row["component"], row["item_key"]))
    for item in items:
        metadata = item.get("metadata") or {}
        if metadata.get("deprecated_since_versions"):
            item["deprecated_since"] = None
    high_confidence_lifecycle = [
        item
        for item in items
        if item.get("deprecated_since") or item.get("removed_since") or (item.get("metadata") or {}).get("deprecated_since_versions")
    ]
    output = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": {
            "docs_repo": str(docs_repo),
            "remote": args.remote,
            "branches": [{"name": branch, "commit": branch_commits[branch]} for branch in branches],
            "scope": str(scope_path),
        },
        "counts": {
            "items": len(items),
            "items_with_lifecycle": len(high_confidence_lifecycle),
            "candidates": len(candidates),
        },
        "items": items,
    }
    candidate_output = {
        "generated_at": output["generated_at"],
        "source": output["source"],
        "counts": {
            "candidates": len(candidates),
            "auto_applied": sum(1 for item in candidates if item["review_status"] == "auto_applied"),
            "needs_review": sum(1 for item in candidates if item["review_status"] == "needs_review"),
        },
        "candidates": sorted(candidates, key=lambda row: (row["content_type"], row["item_key"], row["source_branch"], row["source_line_start"])),
    }

    write_json(pathlib.Path(args.output), output)
    write_json(pathlib.Path(args.candidates), candidate_output)
    print(json.dumps({"output": args.output, "candidates": args.candidates, **output["counts"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
