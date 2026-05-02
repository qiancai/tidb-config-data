# Comparison Read Model

The Config Comparison UI should be backed by a stable read model before adding an API or frontend. The model here supports the screenshot-style workflow:

- select a source version and target version
- select one content type
- compute `new`, `removed`, `modified`, and `unchanged`
- expose `deprecated` as an independent docs-derived flag
- render a searchable/filterable table

See `MVP_DATA_CONTRACT.md` for the stable UI/API response shape.

## Content Types

The MVP supports these content types:

| Content type | UI label | Source file |
|---|---|---|
| `system_variables` | TiDB System Variables | `normalized/system_variables.json` |
| `tidb_config` | TiDB Config | `normalized/show_config_tidb.json` |
| `tikv_config` | TiKV Config | `normalized/show_config_tikv.json` |
| `tiflash_config` | TiFlash Config | `normalized/show_config_tiflash.json` |
| `pd_config` | PD Config | `normalized/show_config_pd.json` |

## Status Semantics

- `new`: target version has the item, source version does not.
- `removed`: source version has the item, target version does not.
- `modified`: both versions have the item, but one or more comparison fields differ.
- `unchanged`: both versions have the item and comparison fields are equal.

`deprecated` is not a status. It is a metadata flag because an item can be both `modified` and deprecated. It comes from docs metadata, not from raw playground captures.

For system variables, the comparison fields are:

```text
VARIABLE_SCOPE
DEFAULT_VALUE
MIN_VALUE
MAX_VALUE
POSSIBLE_VALUES
IS_NOOP
```

For component configs, the comparison field is:

```text
Value
```

## Offline Comparison

Use the repo-backed script before building or debugging an API:

```bash
scripts/compare-configs.py \
  --from-version v8.1.2 \
  --to-version v8.5.6 \
  --content-type system_variables
```

Return CSV for export-like workflows:

```bash
scripts/compare-configs.py \
  --from-version v8.1.2 \
  --to-version v8.5.6 \
  --content-type tidb_config \
  --format csv
```

Filter the read model:

```bash
scripts/compare-configs.py \
  --from-version v8.1.2 \
  --to-version v8.5.6 \
  --content-type system_variables \
  --status modified \
  --search commit \
  --limit 20
```

The JSON output has this shape:

```json
{
  "from_version": "v8.1.2",
  "to_version": "v8.5.6",
  "content_type": "system_variables",
  "summary": {
    "total": 937,
    "new": 46,
    "removed": 2,
    "modified": 26,
    "unchanged": 863,
    "deprecated": 18
  },
  "filtered_total": 937,
  "rows": [
    {
      "status": "modified",
      "item_key": "tidb_enable_async_commit",
      "from_value": "ON",
      "to_value": "OFF",
      "field_changes": {
        "DEFAULT_VALUE": {
          "from": "ON",
          "to": "OFF"
        }
      }
    }
  ]
}
```

Counts above are examples. Use the script output as the source of truth.

## Database Comparison

After importing to TiDB Cloud Starter, use:

```bash
queries/compare-read-model.sql
```

That file contains SQL read models for:

- system variable comparison
- component config comparison
- summary counts

The SQL is intentionally written as query templates with `@from_version`, `@to_version`, and `@component` variables. A future API can either reuse these templates or generate equivalent SQL.

## Metadata

The current metadata table is intentionally sparse:

```text
config_item_metadata
```

The importer seeds basic metadata from `VARIABLES_INFO` and `SHOW CONFIG`, such as item key, component, scope, and inferred value type. `scripts/extract-doc-metadata.py` enriches it from docs with:

- description
- docs URL
- new since
- deprecated since
- removed since
- replacement
- persists to cluster
- applies to SET_VAR

Lower-confidence docs matches are kept in `metadata/doc-metadata-candidates.json` for review instead of being auto-applied.
