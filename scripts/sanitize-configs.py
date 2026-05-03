#!/usr/bin/env python3
"""Create a sanitized copy of a TiDB config capture directory."""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
from typing import Any

from _common import infer_cluster_tag
from _common import sanitize_rules as common_sanitize_rules
from _common import sanitize_text
from _common import sha256


TEXT_SUFFIXES = {".json", ".tsv", ".toml", ".md", ".txt", ".yaml", ".yml"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir")
    parser.add_argument("target_dir")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--cluster-tag", help="TiUP playground tag. Defaults to a value inferred from source_dir")
    parser.add_argument("--extra-replace", action="append", default=[], metavar="FROM=TO")
    return parser.parse_args()


def sanitize_rules(args: argparse.Namespace) -> list[tuple[str, str]]:
    return common_sanitize_rules(args.cluster_tag, args.extra_replace)


def update_manifest(target: pathlib.Path, rules: list[tuple[str, str]]) -> None:
    manifest_path = target / "manifest.json"
    if not manifest_path.exists():
        return
    data: dict[str, Any] = json.loads(manifest_path.read_text())
    data["sanitized"] = True
    data["sanitize_rules"] = [{"from": src, "to": dst} for src, dst in rules]
    files = []
    for path in sorted(p for p in target.rglob("*") if p.is_file()):
        rel = path.relative_to(target).as_posix()
        if rel in {"manifest.json", "SHA256SUMS"}:
            continue
        files.append({"path": rel, "bytes": path.stat().st_size, "sha256": sha256(path)})
    data["files"] = files
    manifest_path.write_text(
        sanitize_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", rules),
        encoding="utf-8",
    )


def write_sha256sums(target: pathlib.Path) -> None:
    lines = []
    for path in sorted(p for p in target.rglob("*") if p.is_file()):
        rel = path.relative_to(target).as_posix()
        if rel == "SHA256SUMS":
            continue
        lines.append(f"{sha256(path)}  ./{rel}\n")
    (target / "SHA256SUMS").write_text("".join(lines))


def main() -> int:
    args = parse_args()
    source = pathlib.Path(args.source_dir).resolve()
    target = pathlib.Path(args.target_dir).resolve()
    if not source.exists():
        raise SystemExit(f"source does not exist: {source}")
    args.cluster_tag = args.cluster_tag or infer_cluster_tag(source)
    if target.exists():
        if not args.force:
            raise SystemExit(f"target exists: {target}; pass --force to overwrite")
        shutil.rmtree(target)
    rules = sanitize_rules(args)
    for src in sorted(p for p in source.rglob("*") if p.is_file()):
        rel = src.relative_to(source)
        if rel.name == ".DS_Store":
            continue
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix in TEXT_SUFFIXES:
            text = src.read_text(encoding="utf-8", errors="replace")
            dst.write_text(sanitize_text(text, rules), encoding="utf-8")
        else:
            shutil.copy2(src, dst)
    update_manifest(target, rules)
    write_sha256sums(target)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
