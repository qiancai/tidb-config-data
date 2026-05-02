# tidb-config-data

This repository stores sanitized TiDB configuration capture data collected from local TiUP playground clusters.

The data is intended for comparing TiDB system variables and component configuration across selected TiDB versions. It is not production cluster telemetry and should not be used as evidence of customer deployment settings.

## Documentation Map

- `README.md`: repository overview, dataset index, and data layout.
- `COLLECTING.md`: reproducible capture workflow, scripts, endpoints, cleanup, and validation steps.
- `DATABASE.md`: TiDB Cloud Starter schema, import workflow, and persistence policy.
- `COMPARISON.md`: comparison status semantics, read model, and query/script usage.
- `v*/SUMMARY.md`: generated summary for one captured TiDB version.

## Current MVP Dataset

The first dataset covers representative modern LTS versions:

| Version | System variables | SHOW CONFIG | TiDB | TiKV | PD | TiFlash |
|---|---:|---:|---:|---:|---:|---:|
| v6.5.12 | 799 | 1666 | 175 | 659 | 155 | 677 |
| v7.1.6 | 850 | 1692 | 192 | 669 | 174 | 657 |
| v7.5.0 | 875 | 1741 | 196 | 685 | 181 | 679 |
| v7.5.7 | 876 | 1746 | 200 | 687 | 180 | 679 |
| v8.1.0 | 891 | 1797 | 203 | 709 | 185 | 700 |
| v8.1.2 | 891 | 1806 | 203 | 705 | 186 | 712 |
| v8.5.0 | 904 | 1784 | 200 | 710 | 156 | 718 |
| v8.5.6 | 935 | 1801 | 206 | 718 | 158 | 719 |

The version scope is recorded in `mvp-versions.json`. The broader TiDB self-managed release list is recorded in `versions.json`.

## Data Layout

Each version directory follows this structure:

```text
v8.5.6/
  SUMMARY.md
  manifest.json
  SHA256SUMS
  normalized/
    system_variables.json
    show_config.json
    show_config_tidb.json
    show_config_tikv.json
    show_config_pd.json
    show_config_tiflash.json
  raw-sanitized/
    tidb/tidb_config.json
    tikv/tikv_config_full.json
    pd/pd_config.json
    tiflash/tiflash_config_full.json
    tiflash/files/
      tiflash.toml
      tiflash_proxy.toml
      last_tikv.toml
```

Use `normalized/` for comparison, indexing, and database import. Use `raw-sanitized/` as the source snapshot fallback when normalized output misses details.

## Database Layer

The Git repository is the canonical source of truth. A TiDB Cloud Starter database can be used as a rebuildable query layer for search, diff, APIs, and UI workflows.

See `DATABASE.md` for the schema and import flow:

```bash
scripts/import-tidb.py --ssl --reset
```

See `COMPARISON.md` for the comparison read model:

```bash
scripts/compare-configs.py --from-version v8.1.2 --to-version v8.5.6 --content-type system_variables
```

## Data Sources

- System variables: `INFORMATION_SCHEMA.VARIABLES_INFO`
- Normalized component config: `SHOW CONFIG`
- TiDB raw config: `http://127.0.0.1:10080/config`
- TiKV raw config: `http://127.0.0.1:20180/config?full=true`
- PD raw config: `http://127.0.0.1:2379/pd/api/v1/config`
- TiFlash raw config: `http://127.0.0.1:20292/config?full=true`
- TiFlash config files: playground-generated TiFlash TOML files

## Privacy

The committed captures are sanitized. Local paths, user names, playground tags, and localhost values are replaced with placeholders such as `${HOME}`, `${TIUP_DATA_DIR}`, `${PLAYGROUND_TAG}`, and `${LOCALHOST}`.

## Validate

```bash
for d in v*/manifest.json; do
  scripts/validate-capture.py "${d%/manifest.json}" --require-sanitized
done
```
