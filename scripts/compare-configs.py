#!/usr/bin/env python3
"""Compare captured TiDB config data between two versions."""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import re
import sys
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]

CONTENT_TYPES = {
    "system_variables": {
        "label": "TiDB System Variables",
        "component": "tidb",
        "path": "normalized/system_variables.json",
        "key": "VARIABLE_NAME",
        "compare_fields": ["VARIABLE_SCOPE", "DEFAULT_VALUE", "MIN_VALUE", "MAX_VALUE", "POSSIBLE_VALUES", "IS_NOOP"],
    },
    "tidb_config": {
        "label": "TiDB Config",
        "component": "tidb",
        "path": "normalized/show_config_tidb.json",
        "key": "Name",
        "compare_fields": ["Value"],
    },
    "tikv_config": {
        "label": "TiKV Config",
        "component": "tikv",
        "path": "normalized/show_config_tikv.json",
        "key": "Name",
        "compare_fields": ["Value"],
    },
    "tiflash_config": {
        "label": "TiFlash Config",
        "component": "tiflash",
        "path": "normalized/show_config_tiflash.json",
        "key": "Name",
        "compare_fields": ["Value"],
    },
    "pd_config": {
        "label": "PD Config",
        "component": "pd",
        "path": "normalized/show_config_pd.json",
        "key": "Name",
        "compare_fields": ["Value"],
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--from-version", required=True)
    parser.add_argument("--to-version", required=True)
    parser.add_argument("--content-type", choices=sorted(CONTENT_TYPES), default="system_variables")
    parser.add_argument("--status", action="append", choices=["new", "removed", "modified", "unchanged"], default=[])
    parser.add_argument("--search", help="Case-insensitive substring search over key, change note, and values")
    parser.add_argument("--limit", type=int, default=0, help="Maximum rows to emit; 0 means no limit")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--format", choices=["json", "csv"], default="json")
    return parser.parse_args()


def normalize_version(version: str) -> str:
    return version if version.startswith("v") else f"v{version}"


def version_tuple(version: str) -> tuple[int, ...]:
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        return ()
    return tuple(int(part) for part in match.groups())


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def display_value(value: Any) -> str:
    normalized = normalize_value(value)
    return "-" if normalized is None else normalized


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


def metadata_key(content_type: str, component: str, item_key: str) -> str:
    return f"{content_type}\0{component}\0{item_key}"


def load_release_events(repo_root: pathlib.Path) -> dict[str, list[dict[str, Any]]]:
    path = repo_root / "metadata" / "release-note-events.json"
    if not path.exists():
        return {}
    payload = load_json(path)
    rows = payload.get("events", payload if isinstance(payload, list) else [])
    events: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        content_type = row.get("content_type")
        component = row.get("component", "")
        item_key = row.get("item_key")
        if content_type and item_key:
            events.setdefault(metadata_key(content_type, component, item_key), []).append(row)
    for item_events in events.values():
        item_events.sort(key=lambda row: version_tuple(row.get("version", "")))
    return events


def event_applies_to_target(event_version: str | None, target_version: str) -> bool:
    event = version_tuple(event_version or "")
    target = version_tuple(target_version)
    if not event or not target or event > target:
        return False
    if event[:2] == target[:2]:
        return True
    return event[2] == 0


def event_in_compare_range(event_version: str | None, from_version: str, to_version: str) -> bool:
    event = version_tuple(event_version or "")
    source = version_tuple(from_version)
    target = version_tuple(to_version)
    if not event or not source or not target or source == target:
        return False
    low, high = sorted([source, target])
    return low < event <= high


def active_release_event_since(events: list[dict[str, Any]], to_version: str, event_type: str) -> str | None:
    for event in events:
        if event.get("event_type") == event_type and event_applies_to_target(event.get("version"), to_version):
            return event.get("version")
    return None


def release_events_in_range(events: list[dict[str, Any]], from_version: str, to_version: str) -> list[dict[str, Any]]:
    actionable = {"modified", "removed", "deprecated"}
    return [
        event
        for event in events
        if event.get("event_type") in actionable and event_in_compare_range(event.get("version"), from_version, to_version)
    ]


def change_note_from_events(events: list[dict[str, Any]]) -> str | None:
    if not events:
        return None
    if len(events) == 1:
        return events[0].get("change_note")
    notes = []
    for event in events:
        note = event.get("change_note")
        if note:
            notes.append(f"{event.get('version')}: {note}")
    return "\n".join(notes) if notes else None


def load_rows(repo_root: pathlib.Path, version: str, content_type: str) -> dict[str, dict[str, Any]]:
    spec = CONTENT_TYPES[content_type]
    path = repo_root / version / spec["path"]
    rows = load_json(path)
    if content_type == "system_variables":
        return {row[spec["key"]]: row for row in rows}
    return collapse_config_rows(rows)


def collapse_config_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["Name"], []).append(row)

    collapsed: dict[str, dict[str, Any]] = {}
    for name, items in grouped.items():
        items = sorted(items, key=lambda row: row.get("Instance") or "")
        values = {row.get("Instance") or "": row.get("Value") for row in items}
        distinct = {normalize_value(value) for value in values.values()}
        value: Any
        if len(distinct) == 1:
            value = items[0].get("Value")
        else:
            value = values
        collapsed[name] = {
            "Type": items[0].get("Type"),
            "Instance": ",".join(values),
            "Name": name,
            "Value": value,
            "_instances": values,
            "_rows": items,
        }
    return collapsed


def field_changes(from_row: dict[str, Any] | None, to_row: dict[str, Any] | None, fields: list[str]) -> dict[str, dict[str, Any]]:
    changes: dict[str, dict[str, Any]] = {}
    if from_row is None or to_row is None:
        return changes
    for field in fields:
        old = normalize_value(from_row.get(field))
        new = normalize_value(to_row.get(field))
        if old != new:
            changes[field] = {"from": from_row.get(field), "to": to_row.get(field)}
    return changes


def row_status(from_row: dict[str, Any] | None, to_row: dict[str, Any] | None, changes: dict[str, dict[str, Any]]) -> str:
    if from_row is None and to_row is not None:
        return "new"
    if from_row is not None and to_row is None:
        return "removed"
    if changes:
        return "modified"
    return "unchanged"


def compare(repo_root: pathlib.Path, from_version: str, to_version: str, content_type: str) -> dict[str, Any]:
    from_rows = load_rows(repo_root, from_version, content_type)
    to_rows = load_rows(repo_root, to_version, content_type)
    release_events = load_release_events(repo_root)
    spec = CONTENT_TYPES[content_type]
    component = spec["component"]
    keys = sorted(set(from_rows) | set(to_rows))
    rows = []
    summary = {"total": len(keys), "new": 0, "removed": 0, "modified": 0, "unchanged": 0, "deprecated": 0}

    for item_key in keys:
        from_row = from_rows.get(item_key)
        to_row = to_rows.get(item_key)
        changes = field_changes(from_row, to_row, spec["compare_fields"])
        status = row_status(from_row, to_row, changes)
        item_release_events = release_events.get(metadata_key(content_type, component, item_key), [])
        interval_release_events = release_events_in_range(item_release_events, from_version, to_version)
        effective_row = to_row or from_row or {}
        deprecated_since = active_release_event_since(item_release_events, to_version, "deprecated")
        removed_since = active_release_event_since(item_release_events, to_version, "removed")
        deprecated_since_versions = sorted(
            {
                event["version"]
                for event in item_release_events
                if event.get("event_type") == "deprecated" and event.get("version")
            },
            key=version_tuple,
        )
        active_deprecated_since = deprecated_since
        is_deprecated = active_deprecated_since is not None and to_row is not None
        change_note = change_note_from_events(interval_release_events)
        first_interval_event = interval_release_events[0] if interval_release_events else {}
        summary[status] += 1
        if is_deprecated:
            summary["deprecated"] += 1

        if content_type == "system_variables":
            from_value = from_row.get("DEFAULT_VALUE") if from_row else None
            to_value = to_row.get("DEFAULT_VALUE") if to_row else None
            variable_scope = effective_row.get("VARIABLE_SCOPE")
            value_type = infer_value_type(
                from_value,
                to_value,
                possible_values=effective_row.get("POSSIBLE_VALUES"),
            )
            extra = {
                "scope": variable_scope,
                "from_current_value": from_row.get("CURRENT_VALUE") if from_row else None,
                "to_current_value": to_row.get("CURRENT_VALUE") if to_row else None,
                "possible_values": effective_row.get("POSSIBLE_VALUES"),
                "min_value": effective_row.get("MIN_VALUE"),
                "max_value": effective_row.get("MAX_VALUE"),
                "is_noop": effective_row.get("IS_NOOP"),
            }
        else:
            from_value = from_row.get("Value") if from_row else None
            to_value = to_row.get("Value") if to_row else None
            value_type = infer_value_type(from_value, to_value)
            extra = {
                "scope": None,
                "instances": (to_row or from_row or {}).get("_instances"),
            }

        rows.append(
            {
                "status": status,
                "content_type": content_type,
                "component": component,
                "item_key": item_key,
                "display_name": item_key,
                "value_type": value_type,
                "from_value": from_value,
                "to_value": to_value,
                "field_changes": changes,
                "is_deprecated": is_deprecated,
                "deprecated_since": active_deprecated_since if is_deprecated else None,
                "deprecated_since_versions": deprecated_since_versions,
                "removed_since": removed_since,
                "replacement": first_interval_event.get("replacement"),
                "change_note": change_note,
                "change_note_type": first_interval_event.get("event_type"),
                "change_note_version": first_interval_event.get("version"),
                "change_note_url": first_interval_event.get("release_note_url"),
                "change_note_events": interval_release_events,
                "source": "variables_info" if content_type == "system_variables" else "show_config",
                **extra,
            }
        )

    return {
        "from_version": from_version,
        "to_version": to_version,
        "content_type": content_type,
        "label": spec["label"],
        "summary": summary,
        "rows": rows,
    }


def row_matches(row: dict[str, Any], statuses: set[str], search: str | None) -> bool:
    if statuses and row["status"] not in statuses:
        return False
    if not search:
        return True
    needle = search.lower()
    haystack = " ".join(str(row.get(key) or "") for key in ["item_key", "display_name", "change_note", "from_value", "to_value"]).lower()
    return needle in haystack


def apply_filters(result: dict[str, Any], statuses: set[str], search: str | None, limit: int, offset: int) -> dict[str, Any]:
    filtered = [row for row in result["rows"] if row_matches(row, statuses, search)]
    page = filtered[offset : offset + limit] if limit else filtered[offset:]
    return {
        **result,
        "filters": {"status": sorted(statuses), "search": search, "limit": limit, "offset": offset},
        "filtered_total": len(filtered),
        "rows": page,
    }


def emit_csv(result: dict[str, Any]) -> None:
    writer = csv.DictWriter(
        sys.stdout,
        fieldnames=[
            "status",
            "content_type",
            "component",
            "item_key",
            "display_name",
            "scope",
            "value_type",
            "from_value",
            "to_value",
            "is_deprecated",
            "deprecated_since",
            "removed_since",
            "replacement",
            "change_note",
            "change_note_version",
            "source",
        ],
    )
    writer.writeheader()
    for row in result["rows"]:
        writer.writerow({key: display_value(row.get(key)) for key in writer.fieldnames})


def main() -> int:
    args = parse_args()
    repo_root = pathlib.Path(args.repo_root).resolve()
    from_version = normalize_version(args.from_version)
    to_version = normalize_version(args.to_version)
    result = compare(repo_root, from_version, to_version, args.content_type)
    result = apply_filters(result, set(args.status), args.search, args.limit, args.offset)
    if args.format == "csv":
        emit_csv(result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
