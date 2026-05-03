#!/usr/bin/env python3
"""Import captured TiDB config data into a TiDB/MySQL-compatible database."""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
import pathlib
import re
import subprocess
import sys
from collections.abc import Iterable
from typing import Any

try:
    import pymysql
except ImportError as exc:  # pragma: no cover - dependency check
    raise SystemExit("missing dependency: run `python3 -m pip install pymysql`") from exc


ROOT = pathlib.Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(ROOT), help="Repository root that contains v*/ captures")
    parser.add_argument("--schema", default=str(ROOT / "schema" / "tidb.sql"), help="Schema SQL file")
    parser.add_argument("--host", default=os.environ.get("TIDB_HOST"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("TIDB_PORT", "4000")))
    parser.add_argument("--user", default=os.environ.get("TIDB_USER"))
    parser.add_argument("--database", default=os.environ.get("TIDB_DATABASE"))
    parser.add_argument("--password-env", default="TIDB_PASSWORD", help="Environment variable that stores the password")
    parser.add_argument("--ask-password", action="store_true", help="Prompt for the database password")
    parser.add_argument("--ssl", action="store_true", help="Enable TLS for TiDB Cloud or other TLS-only endpoints")
    parser.add_argument("--ssl-ca", default=os.environ.get("TIDB_SSL_CA"), help="Optional CA certificate path")
    parser.add_argument("--reset", action="store_true", help="Delete existing imported rows before import")
    parser.add_argument("--dry-run", action="store_true", help="Read captures and print import counts without connecting")
    parser.add_argument("--include-raw-payloads", action="store_true", help="Store raw-sanitized payload content in raw_snapshots")
    parser.add_argument("--only-version", action="append", default=[], help="Import only this version; may be repeated")
    return parser.parse_args()


def require_arg(value: str | None, name: str) -> str:
    if not value:
        raise SystemExit(f"missing {name}; pass --{name.replace('_', '-')} or set TIDB_{name.upper()}")
    return value


def password(args: argparse.Namespace) -> str:
    if args.ask_password:
        return getpass.getpass("TiDB password: ")
    value = os.environ.get(args.password_env)
    if value is None:
        raise SystemExit(f"missing password; set {args.password_env} or pass --ask-password")
    return value


def connect(args: argparse.Namespace):
    ssl: dict[str, str] | None = None
    if args.ssl or args.ssl_ca:
        ssl = {}
        if args.ssl_ca:
            ssl["ca"] = args.ssl_ca

    return pymysql.connect(
        host=require_arg(args.host, "host"),
        port=args.port,
        user=require_arg(args.user, "user"),
        password=password(args),
        database=require_arg(args.database, "database"),
        charset="utf8mb4",
        autocommit=False,
        ssl=ssl,
    )


def sql_statements(text: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(current).rstrip(";"))
            current = []
    if current:
        statements.append("\n".join(current))
    return statements


def execute_schema(conn, schema_path: pathlib.Path) -> None:
    with conn.cursor() as cur:
        for stmt in sql_statements(schema_path.read_text(encoding="utf-8")):
            cur.execute(stmt)
    conn.commit()


def reset_tables(conn) -> None:
    tables = [
        "raw_snapshots",
        "config_item_metadata",
        "component_configs",
        "system_variables",
        "cluster_instances",
        "capture_files",
        "capture_versions",
    ]
    with conn.cursor() as cur:
        for table in tables:
            cur.execute(f"DELETE FROM {table}")
    conn.commit()


def sha256(path: pathlib.Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def git_commit(repo_root: pathlib.Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:  # noqa: BLE001
        return None


def version_parts(version: str) -> tuple[int | None, int | None, int | None]:
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        return None, None, None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def release_metadata(repo_root: pathlib.Path) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    versions_path = repo_root / "versions.json"
    if versions_path.exists():
        for item in load_json(versions_path).get("versions", []):
            metadata[item["version"]] = dict(item)

    scope_path = repo_root / "mvp-versions.json"
    if scope_path.exists():
        scope = load_json(scope_path)
        for item in scope.get("versions", []):
            merged = metadata.setdefault(item["version"], {})
            merged.update(item)
            merged["capture_scope"] = scope.get("scope")
    return metadata


def file_role(path: str) -> str:
    if path == "SUMMARY.md":
        return "summary"
    if path.startswith("normalized/"):
        return "normalized"
    if path.startswith("raw-sanitized/") or path.startswith("raw/"):
        return "raw"
    return "metadata"


def raw_source(path: str) -> str:
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] in {"raw-sanitized", "raw"}:
        return parts[1]
    return "unknown"


def raw_payload(path: pathlib.Path) -> tuple[str, str | None, str | None]:
    if path.suffix == ".json":
        return "json", json.dumps(load_json(path), ensure_ascii=False), None
    return "text", None, path.read_text(encoding="utf-8", errors="replace")


def infer_value_type(*values: str | None, possible_values: str | None = None) -> str | None:
    candidates = [value.strip() for value in values if value is not None and value.strip() not in {"", "-"}]
    upper_values = {value.upper() for value in candidates}
    if possible_values:
        possible = {item.strip().upper() for item in possible_values.split(",") if item.strip()}
        if possible and possible <= {"ON", "OFF", "TRUE", "FALSE", "YES", "NO", "0", "1"}:
            return "bool"
        if possible:
            return "enum"
    if upper_values and upper_values <= {"ON", "OFF", "TRUE", "FALSE", "YES", "NO", "0", "1"}:
        return "bool"
    if candidates and all(re.fullmatch(r"[-+]?\d+", value) for value in candidates):
        return "int"
    if candidates and all(re.fullmatch(r"[-+]?(\d+(\.\d*)?|\.\d+)", value) for value in candidates):
        return "float"
    if candidates:
        return "string"
    return None


def batches(items: list[tuple[Any, ...]], size: int = 500) -> Iterable[list[tuple[Any, ...]]]:
    for idx in range(0, len(items), size):
        yield items[idx : idx + size]


def executemany(conn, sql: str, rows: list[tuple[Any, ...]]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        for batch in batches(rows):
            cur.executemany(sql, batch)
    return len(rows)


def import_capture(conn, repo_root: pathlib.Path, capture_dir: pathlib.Path, metadata: dict[str, dict[str, Any]], commit: str | None, include_raw_payloads: bool) -> dict[str, int]:
    version = capture_dir.name
    manifest_path = capture_dir / "manifest.json"
    summary_path = capture_dir / "SUMMARY.md"
    manifest = load_json(manifest_path)
    meta = metadata.get(version, {})
    major, minor, patch = version_parts(version)
    captured_at = manifest.get("captured_at")
    captured_at_mysql = None
    if captured_at:
        captured_at_mysql = dt.datetime.fromisoformat(captured_at).replace(tzinfo=None)

    capture_version_row = (
        version,
        major,
        minor,
        patch,
        meta.get("version_type"),
        meta.get("release_date"),
        meta.get("release_note_url"),
        bool(meta.get("selected_for_capture", True)),
        meta.get("capture_scope"),
        meta.get("reason"),
        captured_at_mysql,
        bool(manifest.get("sanitized")),
        manifest.get("raw_dir"),
        sha256(manifest_path),
        sha256(summary_path) if summary_path.exists() else None,
        commit,
        json.dumps(manifest.get("normalized_counts", {}), ensure_ascii=False),
        json.dumps(manifest, ensure_ascii=False),
    )
    executemany(
        conn,
        """
        INSERT INTO capture_versions (
          version, major, minor, patch, version_type, release_date, release_note_url,
          selected_for_capture, capture_scope, capture_reason, captured_at, sanitized,
          raw_dir, manifest_sha256, summary_sha256, source_git_commit, normalized_counts, manifest
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          major=VALUES(major), minor=VALUES(minor), patch=VALUES(patch),
          version_type=VALUES(version_type), release_date=VALUES(release_date),
          release_note_url=VALUES(release_note_url), selected_for_capture=VALUES(selected_for_capture),
          capture_scope=VALUES(capture_scope), capture_reason=VALUES(capture_reason),
          captured_at=VALUES(captured_at), sanitized=VALUES(sanitized), raw_dir=VALUES(raw_dir),
          manifest_sha256=VALUES(manifest_sha256), summary_sha256=VALUES(summary_sha256),
          source_git_commit=VALUES(source_git_commit), normalized_counts=VALUES(normalized_counts),
          manifest=VALUES(manifest), imported_at=CURRENT_TIMESTAMP(6)
        """,
        [capture_version_row],
    )

    file_rows = []
    for item in manifest.get("files", []):
        file_rows.append((version, item["path"], item["bytes"], item["sha256"], file_role(item["path"])))
    executemany(
        conn,
        """
        INSERT INTO capture_files (version, path, bytes, sha256, file_role)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          bytes=VALUES(bytes), sha256=VALUES(sha256), file_role=VALUES(file_role),
          imported_at=CURRENT_TIMESTAMP(6)
        """,
        file_rows,
    )

    cluster_rows = []
    for row in load_json(capture_dir / "normalized" / "cluster_info.json"):
        cluster_rows.append(
            (
                version,
                row.get("TYPE"),
                row.get("INSTANCE"),
                row.get("STATUS_ADDRESS"),
                row.get("VERSION"),
                row.get("GIT_HASH"),
                row.get("START_TIME"),
                row.get("UPTIME"),
                row.get("SERVER_ID"),
                json.dumps(row, ensure_ascii=False),
            )
        )
    executemany(
        conn,
        """
        INSERT INTO cluster_instances (
          version, component, instance, status_address, component_version, git_hash,
          start_time, uptime, server_id, raw_row
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          status_address=VALUES(status_address), component_version=VALUES(component_version),
          git_hash=VALUES(git_hash), start_time=VALUES(start_time), uptime=VALUES(uptime),
          server_id=VALUES(server_id), raw_row=VALUES(raw_row), imported_at=CURRENT_TIMESTAMP(6)
        """,
        cluster_rows,
    )

    variable_rows = []
    metadata_rows = []
    for row in load_json(capture_dir / "normalized" / "system_variables.json"):
        variable_rows.append(
            (
                version,
                row.get("VARIABLE_NAME"),
                row.get("VARIABLE_SCOPE"),
                row.get("DEFAULT_VALUE"),
                row.get("CURRENT_VALUE"),
                row.get("MIN_VALUE"),
                row.get("MAX_VALUE"),
                row.get("POSSIBLE_VALUES"),
                row.get("IS_NOOP"),
                json.dumps(row, ensure_ascii=False),
            )
        )
        metadata_rows.append(
            (
                "system_variables",
                "tidb",
                row.get("VARIABLE_NAME"),
                row.get("VARIABLE_NAME"),
                infer_value_type(row.get("DEFAULT_VALUE"), row.get("CURRENT_VALUE"), possible_values=row.get("POSSIBLE_VALUES")),
                row.get("VARIABLE_SCOPE"),
                "variables_info",
                json.dumps({"seen_in": [version]}, ensure_ascii=False),
            )
        )
    executemany(
        conn,
        """
        INSERT INTO system_variables (
          version, variable_name, variable_scope, default_value, current_value,
          min_value, max_value, possible_values, is_noop, raw_row
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          variable_scope=VALUES(variable_scope), default_value=VALUES(default_value),
          current_value=VALUES(current_value), min_value=VALUES(min_value),
          max_value=VALUES(max_value), possible_values=VALUES(possible_values),
          is_noop=VALUES(is_noop), raw_row=VALUES(raw_row), imported_at=CURRENT_TIMESTAMP(6)
        """,
        variable_rows,
    )

    config_rows = []
    for row in load_json(capture_dir / "normalized" / "show_config.json"):
        component = row.get("Type")
        name = row.get("Name")
        config_rows.append(
            (
                version,
                component,
                row.get("Instance"),
                name,
                row.get("Value"),
                json.dumps(row, ensure_ascii=False),
            )
        )
        metadata_rows.append(
            (
                f"{component}_config",
                component,
                name,
                name,
                infer_value_type(row.get("Value")),
                None,
                "show_config",
                json.dumps({"seen_in": [version]}, ensure_ascii=False),
            )
        )
    executemany(
        conn,
        """
        INSERT INTO component_configs (version, component, instance, name, value, raw_row)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          value=VALUES(value), raw_row=VALUES(raw_row), imported_at=CURRENT_TIMESTAMP(6)
        """,
        config_rows,
    )

    executemany(
        conn,
        """
        INSERT INTO config_item_metadata (
          content_type, component, item_key, display_name, value_type,
          variable_scope, source, metadata
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          display_name=COALESCE(config_item_metadata.display_name, VALUES(display_name)),
          value_type=COALESCE(config_item_metadata.value_type, VALUES(value_type)),
          variable_scope=COALESCE(config_item_metadata.variable_scope, VALUES(variable_scope)),
          source=IF(config_item_metadata.source IN ('variables_info', 'show_config'), VALUES(source), config_item_metadata.source),
          metadata=COALESCE(VALUES(metadata), config_item_metadata.metadata),
          imported_at=CURRENT_TIMESTAMP(6)
        """,
        [row for row in metadata_rows if row[2] and row[3] and row[1]],
    )

    raw_rows = []
    for item in manifest.get("files", []):
        path = item["path"]
        if not (path.startswith("raw-sanitized/") or path.startswith("raw/")):
            continue
        payload_kind = "none"
        payload_json = None
        payload_text = None
        if include_raw_payloads:
            payload_kind, payload_json, payload_text = raw_payload(capture_dir / path)
        raw_rows.append((version, raw_source(path), path, item["sha256"], item["bytes"], payload_kind, payload_json, payload_text))
    executemany(
        conn,
        """
        INSERT INTO raw_snapshots (
          version, source, path, sha256, bytes, payload_kind, payload_json, payload_text
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          source=VALUES(source), sha256=VALUES(sha256), bytes=VALUES(bytes),
          payload_kind=VALUES(payload_kind), payload_json=VALUES(payload_json),
          payload_text=VALUES(payload_text), imported_at=CURRENT_TIMESTAMP(6)
        """,
        raw_rows,
    )

    return {
        "capture_files": len(file_rows),
        "cluster_instances": len(cluster_rows),
        "system_variables": len(variable_rows),
        "component_configs": len(config_rows),
        "config_item_metadata": len({(row[0], row[1], row[2]) for row in metadata_rows if row[2] and row[3] and row[1]}),
        "raw_snapshots": len(raw_rows),
    }


def capture_dirs(repo_root: pathlib.Path, only_versions: list[str]) -> list[pathlib.Path]:
    wanted = {v if v.startswith("v") else f"v{v}" for v in only_versions}
    dirs = [p for p in sorted(repo_root.glob("v*")) if p.is_dir() and (p / "manifest.json").exists()]
    if wanted:
        dirs = [p for p in dirs if p.name in wanted]
    return dirs


def capture_counts(capture_dir: pathlib.Path) -> dict[str, int]:
    manifest = load_json(capture_dir / "manifest.json")
    return {
        "capture_files": len(manifest.get("files", [])),
        "cluster_instances": len(load_json(capture_dir / "normalized" / "cluster_info.json")),
        "system_variables": len(load_json(capture_dir / "normalized" / "system_variables.json")),
        "component_configs": len(load_json(capture_dir / "normalized" / "show_config.json")),
        "config_item_metadata": len(load_json(capture_dir / "normalized" / "system_variables.json"))
        + len({(row.get("Type"), row.get("Name")) for row in load_json(capture_dir / "normalized" / "show_config.json")}),
        "raw_snapshots": sum(
            1
            for item in manifest.get("files", [])
            if item["path"].startswith("raw-sanitized/") or item["path"].startswith("raw/")
        ),
    }


def main() -> int:
    args = parse_args()
    repo_root = pathlib.Path(args.repo_root).resolve()
    schema_path = pathlib.Path(args.schema).resolve()
    dirs = capture_dirs(repo_root, args.only_version)
    if not dirs:
        raise SystemExit("no capture directories found")

    if args.dry_run:
        totals = {"versions": 0, "capture_files": 0, "cluster_instances": 0, "system_variables": 0, "component_configs": 0, "config_item_metadata": 0, "raw_snapshots": 0}
        for capture_dir in dirs:
            counts = capture_counts(capture_dir)
            totals["versions"] += 1
            for key, value in counts.items():
                totals[key] += value
            print(json.dumps({"version": capture_dir.name, **counts}, ensure_ascii=False))
        print(json.dumps({"would_import": totals}, ensure_ascii=False, indent=2))
        return 0

    conn = connect(args)
    try:
        execute_schema(conn, schema_path)
        if args.reset:
            reset_tables(conn)

        metadata = release_metadata(repo_root)
        commit = git_commit(repo_root)
        totals = {"versions": 0, "capture_files": 0, "cluster_instances": 0, "system_variables": 0, "component_configs": 0, "config_item_metadata": 0, "raw_snapshots": 0}
        for capture_dir in dirs:
            counts = import_capture(conn, repo_root, capture_dir, metadata, commit, args.include_raw_payloads)
            conn.commit()
            totals["versions"] += 1
            for key, value in counts.items():
                totals[key] += value
            print(json.dumps({"version": capture_dir.name, **counts}, ensure_ascii=False))

        conn.commit()
        print(json.dumps({"imported": totals}, ensure_ascii=False, indent=2))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
