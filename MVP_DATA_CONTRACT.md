# MVP Data Contract

This document defines the stable read model for the first Config Comparison UI.

The UI should not read raw capture files, docs branches, or database tables directly. It should consume an API or generated static JSON that follows this contract. For local development, `scripts/compare-configs.py` is the reference implementation.

## Inputs

```text
from_version: v8.1.2
to_version: v8.5.6
content_type: system_variables | tidb_config | tikv_config | tiflash_config | pd_config
status: new | removed | modified | unchanged
search: string
limit: number
offset: number
format: json | csv
```

`status`, `search`, `limit`, and `offset` are optional.

## Content Types

| Content type | UI label | Component |
|---|---|---|
| `system_variables` | TiDB System Variables | `tidb` |
| `tidb_config` | TiDB Config | `tidb` |
| `tikv_config` | TiKV Config | `tikv` |
| `tiflash_config` | TiFlash Config | `tiflash` |
| `pd_config` | PD Config | `pd` |

## Response Shape

```json
{
  "from_version": "v8.1.2",
  "to_version": "v8.5.6",
  "content_type": "system_variables",
  "label": "TiDB System Variables",
  "summary": {
    "total": 937,
    "new": 46,
    "removed": 2,
    "modified": 26,
    "unchanged": 863,
    "deprecated": 18
  },
  "filters": {
    "status": ["modified"],
    "search": "commit",
    "limit": 20,
    "offset": 0
  },
  "filtered_total": 3,
  "rows": []
}
```

## Row Shape

Every row uses the same common fields:

```json
{
  "status": "modified",
  "content_type": "system_variables",
  "component": "tidb",
  "item_key": "tidb_disable_txn_auto_retry",
  "display_name": "tidb_disable_txn_auto_retry",
  "value_type": "bool",
  "from_value": "ON",
  "to_value": "ON",
  "field_changes": {},
  "is_deprecated": true,
  "deprecated_since": "v8.0.0",
  "deprecated_since_versions": [],
  "removed_since": null,
  "replacement": null,
  "change_note": "Starting from v8.5.6, statistics Version 1 (`tidb_analyze_version = 1`) is deprecated and will be removed in a future release.",
  "change_note_type": "deprecated",
  "change_note_version": "v8.5.6",
  "change_note_url": "https://docs.pingcap.com/tidb/v8.5/release-8.5.6",
  "source": "variables_info"
}
```

System-variable rows also include:

```json
{
  "scope": "SESSION | GLOBAL",
  "from_current_value": "ON",
  "to_current_value": "ON",
  "possible_values": "ON,OFF",
  "min_value": null,
  "max_value": null,
  "is_noop": "NO"
}
```

Component-config rows also include:

```json
{
  "scope": null,
  "instances": {
    "${LOCALHOST}:4000": "true"
  }
}
```

## Status Semantics

- `new`: target version has the item, source version does not.
- `removed`: source version has the item, target version does not.
- `modified`: both versions have the item, but comparison fields differ.
- `unchanged`: both versions have the item and comparison fields are equal.

`deprecated` is not a row status. It is a release-note-derived independent flag for the target version.

## Change Notes

`change_note` is derived only from release notes in the compare interval. For example, comparing `v8.5.0` to `v8.5.6` reads release notes from `v8.5.1` through `v8.5.6`. The diff status (`new`, `removed`, `modified`, or `unchanged`) still comes only from captured config data.

Release-note events use `modified`, `removed`, and `deprecated` as event types. If no release note matches an item in the compare interval, `change_note` is empty instead of falling back to ordinary configuration docs.

Release-note events are extracted from the `release-8.5` branch of the docs repository by default.

## MVP Source Of Truth

For the MVP:

- Git data in this repository is the canonical source of truth.
- `metadata/release-note-events.json` is included in the read model for change notes, removals, and deprecations.
- Ordinary configuration docs are not included in the MVP read model.
- TiDB Cloud Starter can be used later as a query layer, but the UI contract should remain the same.

## Reference Commands

Generate a JSON payload:

```bash
scripts/compare-configs.py \
  --from-version v8.1.2 \
  --to-version v8.5.6 \
  --content-type system_variables \
  --limit 20
```

Generate CSV export:

```bash
scripts/compare-configs.py \
  --from-version v8.1.2 \
  --to-version v8.5.6 \
  --content-type tidb_config \
  --format csv
```
