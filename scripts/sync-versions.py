#!/usr/bin/env python3
"""Sync TiDB self-managed release versions from docs.pingcap.com."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import urllib.request


ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE_URL = "https://docs.pingcap.com/releases/tidb-self-managed.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", default=SOURCE_URL)
    parser.add_argument("--output", default=str(ROOT / "versions.json"))
    parser.add_argument("--include-dmr", action="store_true", help="Mark DMR versions selected for capture")
    return parser.parse_args()


def version_type(label: str) -> str:
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
    if re.match(r"^(6\.5|7\.1|7\.5|8\.1|8\.5)\.", label):
        return "LTS"
    return "GA"


def main() -> int:
    args = parse_args()
    with urllib.request.urlopen(args.source_url, timeout=30) as resp:
        text = resp.read().decode("utf-8")
    body = text.split("---", 2)[-1]
    versions = []
    pattern = re.compile(r"^- \[([^\]]+)\]\((/releases/release-[^)]+\.md)\):\s*([^\n]+)", re.M)
    for match in pattern.finditer(body):
        label, href, date_text = match.groups()
        kind = version_type(label)
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
