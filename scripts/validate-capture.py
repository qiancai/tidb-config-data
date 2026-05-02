#!/usr/bin/env python3
"""Validate a TiDB config capture directory."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import sys


REQUIRED = [
    "SUMMARY.md",
    "manifest.json",
    "SHA256SUMS",
    "normalized/version.json",
    "normalized/cluster_info.json",
    "normalized/system_variables.json",
    "normalized/show_config.json",
    "normalized/show_config_tidb.json",
    "normalized/show_config_tikv.json",
    "normalized/show_config_pd.json",
    "normalized/show_config_tiflash.json",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_dir")
    parser.add_argument("--require-sanitized", action="store_true")
    return parser.parse_args()


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: pathlib.Path):
    with path.open() as f:
        return json.load(f)


def check_sha256sums(base: pathlib.Path, errors: list[str]) -> None:
    sums = base / "SHA256SUMS"
    for line in sums.read_text().splitlines():
        if not line.strip():
            continue
        expected, rel = line.split(None, 1)
        rel = rel.removeprefix("./")
        path = base / rel
        if not path.exists():
            errors.append(f"SHA256 target missing: {rel}")
            continue
        actual = sha256(path)
        if actual != expected:
            errors.append(f"SHA256 mismatch: {rel}")


def main() -> int:
    args = parse_args()
    base = pathlib.Path(args.capture_dir).resolve()
    errors: list[str] = []
    if not base.exists():
        raise SystemExit(f"capture dir does not exist: {base}")

    for rel in REQUIRED:
        if not (base / rel).exists():
            errors.append(f"missing required file: {rel}")

    if not errors:
        manifest = load_json(base / "manifest.json")
        if args.require_sanitized and not manifest.get("sanitized"):
            errors.append("manifest.sanitized is not true")

        for path in base.rglob("*.json"):
            try:
                load_json(path)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"invalid json: {path.relative_to(base)}: {exc}")

        counts = manifest.get("normalized_counts", {})
        for name, expected in counts.items():
            json_path = base / "normalized" / f"{name}.json"
            tsv_path = base / "normalized" / f"{name}.tsv"
            if json_path.exists():
                actual = len(load_json(json_path))
                if actual != expected:
                    errors.append(f"count mismatch {name}.json: expected {expected}, got {actual}")
            if tsv_path.exists():
                with tsv_path.open(newline="") as f:
                    actual = sum(1 for _ in csv.DictReader(f, delimiter="\t"))
                if actual != expected:
                    errors.append(f"count mismatch {name}.tsv: expected {expected}, got {actual}")

        check_sha256sums(base, errors)

        if args.require_sanitized:
            suspicious = ["/Users/", "127.0.0.1", "tidb-v856"]
            for path in base.rglob("*"):
                if not path.is_file() or path.name == "SHA256SUMS":
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                for marker in suspicious:
                    if marker in text:
                        errors.append(f"unsanitized marker {marker!r} in {path.relative_to(base)}")
                        break

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1
    print(f"OK: {base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
