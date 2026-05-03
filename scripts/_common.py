"""Shared helpers for TiDB config data scripts."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
from typing import Any, Iterable


NULL_MARKER = r"\N"
PLACEHOLDER_PREFIX = "${"


def normalize_version(version: str) -> str:
    return version if version.startswith("v") else f"v{version}"


def default_cluster_tag(version: str) -> str:
    cleaned = normalize_version(version).lstrip("v").replace(".", "").replace("-", "")
    return f"tidb-v{cleaned}"


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sanitize_rules(cluster_tag: str, extra_replace: Iterable[str] = ()) -> list[tuple[str, str]]:
    home = pathlib.Path.home().as_posix()
    rules = [
        (f"{home}/.tiup/data/{cluster_tag}", "${TIUP_DATA_DIR}"),
        (f"{home}/.tiup", "${TIUP_HOME}"),
        (f"{home}/Documents/for-testing", "${PLAYGROUND_WORKDIR}"),
        (f"{home}/Documents/GitHub", "${WORKSPACE}"),
        (home, "${HOME}"),
        (cluster_tag, "${PLAYGROUND_TAG}"),
        ("127.0.0.1", "${LOCALHOST}"),
        ("localhost", "${LOCALHOST_NAME}"),
    ]
    user = os.environ.get("USER")
    if user:
        rules.append((user, "${USER}"))
    for item in extra_replace:
        if "=" not in item:
            raise SystemExit(f"--extra-replace must be FROM=TO, got {item!r}")
        src, dst = item.split("=", 1)
        rules.append((src, dst))
    return [(src, dst) for src, dst in rules if src]


def sanitize_text(text: str, rules: list[tuple[str, str]]) -> str:
    for src, dst in rules:
        text = text.replace(src, dst)
    return text


def sanitize_rows(rows: list[dict[str, Any]], rules: list[tuple[str, str]] | None) -> list[dict[str, Any]]:
    if not rules:
        return rows
    return json.loads(sanitize_text(json.dumps(rows, ensure_ascii=False), rules))


def infer_cluster_tag(capture_dir: pathlib.Path) -> str:
    manifest_path = capture_dir / "manifest.json"
    if manifest_path.exists():
        manifest = load_json(manifest_path)
        cluster_tag = str(manifest.get("cluster_tag") or "")
        if cluster_tag and not cluster_tag.startswith(PLACEHOLDER_PREFIX):
            return cluster_tag
        version = str(manifest.get("version") or "")
        if version:
            return default_cluster_tag(version)
    return default_cluster_tag(capture_dir.name)


def metadata_relpath(path: pathlib.Path, base: pathlib.Path) -> str:
    return os.path.relpath(path, base)


def infer_value_type(*values: Any, possible_values: str | None = None) -> str | None:
    candidates = [str(value).strip() for value in values if value is not None and str(value).strip() not in {"", "-"}]
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
