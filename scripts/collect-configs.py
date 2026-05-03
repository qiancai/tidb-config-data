#!/usr/bin/env python3
"""Collect TiDB playground configuration data from a running cluster."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import getpass
import io
import json
import os
import pathlib
import shutil
import subprocess
import urllib.request
from typing import Any

from _common import NULL_MARKER
from _common import default_cluster_tag
from _common import sanitize_rows
from _common import sanitize_rules as common_sanitize_rules
from _common import sanitize_text
from _common import sha256


ROOT = pathlib.Path(__file__).resolve().parents[1]


NORMALIZED_QUERIES = {
    "version": "SELECT VERSION() AS tidb_version",
    "cluster_info": "SELECT * FROM INFORMATION_SCHEMA.CLUSTER_INFO ORDER BY TYPE, INSTANCE",
    "system_variables": (
        "SELECT VARIABLE_NAME, VARIABLE_SCOPE, DEFAULT_VALUE, CURRENT_VALUE, "
        "MIN_VALUE, MAX_VALUE, POSSIBLE_VALUES, IS_NOOP "
        "FROM INFORMATION_SCHEMA.VARIABLES_INFO ORDER BY VARIABLE_NAME"
    ),
    "show_config": "SHOW CONFIG",
    "show_config_tidb": "SHOW CONFIG WHERE Type='tidb'",
    "show_config_tikv": "SHOW CONFIG WHERE Type='tikv'",
    "show_config_pd": "SHOW CONFIG WHERE Type='pd'",
    "show_config_tiflash": "SHOW CONFIG WHERE Type='tiflash'",
}

NORMALIZED_COLUMNS = {
    "version": ["tidb_version"],
    "cluster_info": ["TYPE", "INSTANCE", "STATUS_ADDRESS", "VERSION", "GIT_HASH", "START_TIME", "UPTIME", "SERVER_ID"],
    "system_variables": [
        "VARIABLE_NAME",
        "VARIABLE_SCOPE",
        "DEFAULT_VALUE",
        "CURRENT_VALUE",
        "MIN_VALUE",
        "MAX_VALUE",
        "POSSIBLE_VALUES",
        "IS_NOOP",
    ],
    "show_config": ["Type", "Instance", "Name", "Value"],
    "show_config_tidb": ["Type", "Instance", "Name", "Value"],
    "show_config_tikv": ["Type", "Instance", "Name", "Value"],
    "show_config_pd": ["Type", "Instance", "Name", "Value"],
    "show_config_tiflash": ["Type", "Instance", "Name", "Value"],
}

SYSTEM_VARIABLES_JSON_QUERY = """
SELECT JSON_OBJECT(
  'VARIABLE_NAME', VARIABLE_NAME,
  'VARIABLE_SCOPE', VARIABLE_SCOPE,
  'DEFAULT_VALUE', DEFAULT_VALUE,
  'CURRENT_VALUE', CURRENT_VALUE,
  'MIN_VALUE', MIN_VALUE,
  'MAX_VALUE', MAX_VALUE,
  'POSSIBLE_VALUES', POSSIBLE_VALUES,
  'IS_NOOP', IS_NOOP
) AS row_json
FROM INFORMATION_SCHEMA.VARIABLES_INFO
ORDER BY VARIABLE_NAME
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="TiDB version, for example v8.5.6 or 8.5.6")
    parser.add_argument("--output-root", default=str(ROOT), help="Directory that receives <version>/")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing capture directory")
    parser.add_argument("--sanitize", action="store_true", help="Sanitize local paths and local addresses before writing")
    parser.add_argument("--cluster-tag", help="TiUP playground tag, defaults to tidb-v<digits>")
    parser.add_argument("--mysql-host", default="127.0.0.1")
    parser.add_argument("--mysql-port", default="4000")
    parser.add_argument("--mysql-user", default="root")
    parser.add_argument("--mysql-password-env", default="MYSQL_PWD", help="Optional environment variable for MySQL password")
    parser.add_argument("--ask-mysql-password", action="store_true", help="Prompt for a MySQL password")
    parser.add_argument("--skip-version-check", action="store_true", help="Skip the TiDB VERSION() safety check")
    parser.add_argument("--tidb-status", default="http://127.0.0.1:10080/config")
    parser.add_argument("--tikv-status", default="http://127.0.0.1:20180/config?full=true")
    parser.add_argument("--pd-config", default="http://127.0.0.1:2379/pd/api/v1/config")
    parser.add_argument("--tiflash-status", default="http://127.0.0.1:20292/config?full=true")
    parser.add_argument("--tiflash-dir", help="TiFlash instance directory that contains tiflash.toml")
    parser.add_argument("--extra-replace", action="append", default=[], metavar="FROM=TO", help="Additional sanitize replacement")
    return parser.parse_args()


def normalize_version(version: str) -> str:
    return version if version.startswith("v") else f"v{version}"


def build_sanitize_rules(args: argparse.Namespace, cluster_tag: str) -> list[tuple[str, str]]:
    return common_sanitize_rules(cluster_tag, args.extra_replace)


def write_text(path: pathlib.Path, text: str, rules: list[tuple[str, str]] | None = None) -> None:
    if rules:
        text = sanitize_text(text, rules)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: pathlib.Path, payload: Any, rules: list[tuple[str, str]] | None = None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    write_text(path, text, rules)


def mysql_env(args: argparse.Namespace) -> dict[str, str]:
    cached = getattr(args, "_mysql_env", None)
    if cached is not None:
        return cached
    env = os.environ.copy()
    if args.ask_mysql_password:
        env["MYSQL_PWD"] = getpass.getpass("MySQL password: ")
    elif args.mysql_password_env in os.environ:
        env["MYSQL_PWD"] = os.environ[args.mysql_password_env]
    args._mysql_env = env
    return env


def run_mysql(args: argparse.Namespace, sql: str) -> str:
    cmd = [
        "mysql",
        "--comments",
        "--host",
        args.mysql_host,
        "--port",
        str(args.mysql_port),
        "-u",
        args.mysql_user,
        "--batch",
        "--raw",
        "-e",
        sql,
    ]
    return subprocess.check_output(cmd, text=True, env=mysql_env(args))


def run_mysql_json_rows(args: argparse.Namespace, sql: str) -> list[dict[str, Any]]:
    cmd = [
        "mysql",
        "--comments",
        "--host",
        args.mysql_host,
        "--port",
        str(args.mysql_port),
        "-u",
        args.mysql_user,
        "--batch",
        "--raw",
        "--skip-column-names",
        "-e",
        sql,
    ]
    output = subprocess.check_output(cmd, text=True, env=mysql_env(args))
    rows = []
    for line in output.splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def tsv_to_rows(tsv_text: str) -> list[dict[str, str | None]]:
    rows: list[dict[str, str | None]] = []
    reader = csv.DictReader(tsv_text.splitlines(), delimiter="\t")
    for row in reader:
        rows.append({key: (None if val == NULL_MARKER else val) for key, val in row.items()})
    return rows


def rows_to_tsv(rows: list[dict[str, Any]], columns: list[str]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: NULL_MARKER if row.get(column) is None else row.get(column) for column in columns})
    return output.getvalue()


def http_get_json(url: str) -> Any:
    # Local status endpoints must not go through an HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"User-Agent": "tidb-config-capture/0.1"})
    with opener.open(req, timeout=30) as resp:
        data = resp.read()
    return json.loads(data.decode("utf-8"))


def check_tidb_version(args: argparse.Namespace, expected_version: str) -> None:
    if args.skip_version_check:
        return
    rows = tsv_to_rows(run_mysql(args, "SELECT VERSION() AS tidb_version"))
    actual = str(rows[0].get("tidb_version") if rows else "")
    if f"TiDB-{expected_version}" not in actual:
        raise SystemExit(
            f"version mismatch: expected TiDB-{expected_version}, got {actual!r}; "
            "use --skip-version-check only when intentionally collecting from a compatible non-playground source"
        )


def copy_text_file(src: pathlib.Path, dst: pathlib.Path, rules: list[tuple[str, str]] | None) -> bool:
    if not src.exists():
        return False
    text = src.read_text(encoding="utf-8", errors="replace")
    write_text(dst, text, rules)
    return True


def list_files(base: pathlib.Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for path in sorted(p for p in base.rglob("*") if p.is_file()):
        rel = path.relative_to(base).as_posix()
        if rel in {"manifest.json", "SHA256SUMS"}:
            continue
        files.append({"path": rel, "bytes": path.stat().st_size, "sha256": sha256(path)})
    return files


def write_sha256sums(base: pathlib.Path) -> None:
    lines = []
    for path in sorted(p for p in base.rglob("*") if p.is_file()):
        rel = path.relative_to(base).as_posix()
        if rel == "SHA256SUMS":
            continue
        lines.append(f"{sha256(path)}  ./{rel}\n")
    (base / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")


def readme_text(version: str, counts: dict[str, int], sanitize: bool) -> str:
    raw_name = "raw-sanitized" if sanitize else "raw"
    return f"""# TiDB {version} Configuration Capture

Captured from a local TiUP playground cluster.

## Layout

- `manifest.json`: capture metadata, source endpoints, counts, and file hashes.
- `SHA256SUMS`: checksums for captured files.
- `normalized/`: flattened data for comparison and database import.
- `{raw_name}/`: source payloads and TiFlash config file snapshots.

## Counts

- System variables: {counts.get("system_variables", 0)}
- SHOW CONFIG total: {counts.get("show_config", 0)}
- TiDB config rows: {counts.get("show_config_tidb", 0)}
- TiKV config rows: {counts.get("show_config_tikv", 0)}
- PD config rows: {counts.get("show_config_pd", 0)}
- TiFlash config rows: {counts.get("show_config_tiflash", 0)}

## TiFlash Note

TiFlash `/config?full=true` combines `raftstore-proxy` config and the engine-store config file content.
It does not expose the complete C++ engine-store default catalog. Generate that catalog from TiFlash
source and docs separately.
"""


def main() -> int:
    args = parse_args()
    version = normalize_version(args.version)
    cluster_tag = args.cluster_tag or default_cluster_tag(version)
    rules = build_sanitize_rules(args, cluster_tag) if args.sanitize else None
    output_dir = pathlib.Path(args.output_root).resolve() / version
    raw_dir_name = "raw-sanitized" if args.sanitize else "raw"

    if output_dir.exists():
        if not args.force:
            raise SystemExit(f"{output_dir} already exists; pass --force to overwrite")
    check_tidb_version(args, version)

    if output_dir.exists():
        shutil.rmtree(output_dir)

    normalized_dir = output_dir / "normalized"
    raw_dir = output_dir / raw_dir_name
    normalized_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {}
    for name, sql in NORMALIZED_QUERIES.items():
        if name == "system_variables":
            rows = sanitize_rows(run_mysql_json_rows(args, SYSTEM_VARIABLES_JSON_QUERY), rules)
            tsv = rows_to_tsv(rows, NORMALIZED_COLUMNS[name])
        else:
            tsv = run_mysql(args, sql)
            if rules:
                tsv = sanitize_text(tsv, rules)
            rows = tsv_to_rows(tsv)
        if rules:
            tsv = sanitize_text(tsv, rules)
            rows = sanitize_rows(rows, rules)
        write_text(normalized_dir / f"{name}.tsv", tsv, None)
        write_json(normalized_dir / f"{name}.json", rows, None)
        counts[name] = len(rows)

    endpoints = {
        "tidb_status": args.tidb_status,
        "tikv_status_full": args.tikv_status,
        "pd_config": args.pd_config,
        "tiflash_status_full": args.tiflash_status,
    }

    if args.sanitize:
        tidb_raw = raw_dir / "tidb" / "tidb_config.json"
        tikv_raw = raw_dir / "tikv" / "tikv_config_full.json"
        pd_raw = raw_dir / "pd" / "pd_config.json"
        tiflash_raw = raw_dir / "tiflash" / "tiflash_config_full.json"
    else:
        tidb_raw = raw_dir / "tidb" / "127.0.0.1_10080_config.json"
        tikv_raw = raw_dir / "tikv" / "127.0.0.1_20180" / "config_full.json"
        pd_raw = raw_dir / "pd" / "127.0.0.1_2379_pd_api_v1_config.json"
        tiflash_raw = raw_dir / "tiflash" / "127.0.0.1_20292" / "config_full.json"

    write_json(tidb_raw, http_get_json(args.tidb_status), rules)
    write_json(tikv_raw, http_get_json(args.tikv_status), rules)
    write_json(pd_raw, http_get_json(args.pd_config), rules)
    write_json(tiflash_raw, http_get_json(args.tiflash_status), rules)

    tiflash_dir = pathlib.Path(args.tiflash_dir) if args.tiflash_dir else pathlib.Path.home() / ".tiup" / "data" / cluster_tag / "tiflash-0"
    tiflash_files = []
    for src_rel, dst_name in [
        ("tiflash.toml", "tiflash.toml"),
        ("tiflash_proxy.toml", "tiflash_proxy.toml"),
        ("proxy_data/last_tikv.toml", "last_tikv.toml"),
    ]:
        if copy_text_file(tiflash_dir / src_rel, raw_dir / "tiflash" / "files" / dst_name, rules):
            tiflash_files.append(src_rel)

    write_text(output_dir / "SUMMARY.md", readme_text(version, counts, args.sanitize), None)

    manifest = {
        "version": version,
        "cluster_tag": "${PLAYGROUND_TAG}" if args.sanitize else cluster_tag,
        "captured_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(),
        "sanitized": bool(args.sanitize),
        "raw_dir": raw_dir_name,
        "sql_endpoint": {
            "host": "${LOCALHOST}" if args.sanitize else args.mysql_host,
            "port": str(args.mysql_port),
            "user": args.mysql_user,
        },
        "component_endpoints": endpoints,
        "normalized_counts": counts,
        "tiflash_config_dir": "${TIUP_DATA_DIR}/tiflash-0" if args.sanitize else str(tiflash_dir),
        "tiflash_config_files": tiflash_files,
        "sanitize_rules": [{"from": src, "to": dst} for src, dst in (rules or [])],
        "notes": [
            "system_variables comes from INFORMATION_SCHEMA.VARIABLES_INFO.",
            "show_config*.tsv/json comes from SHOW CONFIG and component filters.",
            "raw TiKV and TiFlash config endpoints were captured with full=true.",
            "TiFlash engine-store config endpoint exposes the current config file content, not the full C++ default catalog.",
        ],
    }
    if args.sanitize:
        manifest = json.loads(sanitize_text(json.dumps(manifest), rules or []))
    manifest["files"] = list_files(output_dir)
    write_json(output_dir / "manifest.json", manifest, None)
    write_sha256sums(output_dir)

    print(json.dumps({"output_dir": str(output_dir), "counts": counts, "sanitized": args.sanitize}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
