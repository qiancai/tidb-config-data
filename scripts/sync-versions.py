#!/usr/bin/env python3
"""Sync TiDB self-managed release versions from docs.pingcap.com."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import urllib.request

from _common import load_json


ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE_URL = "https://docs.pingcap.com/releases/tidb-self-managed.md"
DEFAULT_LTS_SERIES = ("6.5", "7.1", "7.5", "8.1", "8.5")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", default=SOURCE_URL)
    parser.add_argument("--output", default=str(ROOT / "versions.json"))
    parser.add_argument("--release-policy", default=str(ROOT / "release-policy.json"))
    parser.add_argument("--include-dmr", action="store_true", help="Mark DMR versions selected for capture")
    return parser.parse_args()


def load_lts_series(path: pathlib.Path) -> set[str]:
    if not path.exists():
        return set(DEFAULT_LTS_SERIES)
    payload = load_json(path)
    return {str(series) for series in payload.get("lts_series", DEFAULT_LTS_SERIES)}


def version_type(label: str, lts_series: set[str]) -> str:
    if label == "Pre-GA":
        return "Pre-GA"
    lower = label.lower()
    if "dmr" in lower:
        return "DMR"
    if "rc" in lower:
        return "RC"
    if "beta" in lower:
        return "Beta"
    if "alpha" in lower:
        return "Alpha"
    normalized_label = label.removeprefix("v")
    if any(normalized_label.startswith(f"{series}.") for series in lts_series):
        return "LTS"
    return "GA"


def main() -> int:
    args = parse_args()
    lts_series = load_lts_series(pathlib.Path(args.release_policy))
    with urllib.request.urlopen(args.source_url, timeout=30) as resp:
        text = resp.read().decode("utf-8")
    body = text.split("---", 2)[-1]
    versions = []
    pattern = re.compile(r"^- \[([^\]]+)\]\((/releases/release-[^)]+\.md)\):\s*([^\n]+)", re.M)
    for match in pattern.finditer(body):
        label, href, date_text = match.groups()
        kind = version_type(label, lts_series)
        versions.append(
            {
                "version": label if label.startswith("v") or label in {"Pre-GA"} or label.startswith("rc") else f"v{label}",
                "release_date": date_text.strip(),
                "release_note_url": f"https://docs.pingcap.com{href}",
                "version_type": kind,
                "selected_for_capture": kind in {"LTS", "GA"} or (args.include_dmr and kind == "DMR"),
            }
        )
    output = pathlib.Path(args.output)
    payload = {
        "source_url": args.source_url,
        "synced_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(),
        "count": len(versions),
        "versions": versions,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {len(versions)} versions to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
