# Database Layer

This repository is the canonical source of truth. The database is a rebuildable query layer for search, diff, API, and UI use cases.

For the MVP, TiDB Cloud Starter is a good target because the data is TiDB-related, the schema is standard SQL/MySQL-compatible, and the dataset is small enough to keep the operational cost low.

## What To Store

Store these on every official capture:

- `capture_versions`: one row per captured TiDB version, with release metadata, capture metadata, normalized counts, and manifest JSON.
- `system_variables`: rows from `normalized/system_variables.json`.
- `component_configs`: rows from `normalized/show_config.json`.
- `cluster_instances`: rows from `normalized/cluster_info.json`.
- `capture_files`: file index from `manifest.json`, including path, size, and SHA-256.
- `raw_snapshots`: raw file index for `raw-sanitized/`.
- `config_item_metadata`: sparse product metadata used by the comparison UI.
  - The importer seeds basic metadata from captures.
  - If `metadata/config-item-metadata.json` exists, the importer overlays docs-derived metadata such as descriptions, docs links, `new_since`, `deprecated_since`, replacements, and source traceability.

By default, raw payload content is not imported into the database. The DB stores `path`, `sha256`, and `bytes`; the Git repository keeps the actual sanitized raw snapshots. Use `--include-raw-payloads` only if a later API/UI flow needs raw endpoint payloads directly from SQL.

Do not store these in the database:

- Unsanitized captures.
- TiUP data directories, downloaded component packages, or playground logs.
- Failed or partial trial captures.
- Duplicate captures for the same version unless the capture method changed and the old one is intentionally retained.

## Schema

The schema is in:

```bash
schema/tidb.sql
```

The current MVP schema is intentionally denormalized enough to make version diff queries simple:

- `system_variables` uses `(version, variable_name)` as the primary key.
- `component_configs` uses `(version, component, instance, name)` as the primary key.
- `capture_files` and `raw_snapshots` keep checksums so the database can be traced back to Git artifacts.
- `config_item_metadata` starts with derived metadata and can later be enriched from docs and code.

## Import To TiDB Cloud Starter

Install the Python dependency:

```bash
python3 -m pip install pymysql
```

Set connection environment variables:

```bash
export TIDB_HOST='gateway01.us-west-2.prod.aws.tidbcloud.com'
export TIDB_PORT='4000'
export TIDB_USER='your_user'
export TIDB_DATABASE='tidb_config'
export TIDB_PASSWORD='your_password'
```

Import or rebuild the derived tables:

```bash
scripts/import-tidb.py --ssl --reset
```

Preview the rows that would be imported without connecting:

```bash
scripts/import-tidb.py --dry-run
```

If TiDB Cloud provides a CA file and your local environment requires it:

```bash
export TIDB_SSL_CA='/path/to/ca.pem'
scripts/import-tidb.py --ssl --reset
```

Import one version only:

```bash
scripts/import-tidb.py --ssl --only-version v8.5.6
```

Import raw payload content as well as the raw file index:

```bash
scripts/import-tidb.py --ssl --include-raw-payloads
```

## Query Examples

Example MVP queries live in:

```bash
queries/mvp-examples.sql
```

Comparison read-model queries live in:

```bash
queries/compare-read-model.sql
```

They cover:

- Dataset counts by version.
- History of one system variable.
- System variables whose defaults changed across versions.
- History of one component config.
- Component config values that vary across versions.
- Config items present in one version but absent in another.
- Screenshot-style comparison rows and summary counts.

## Docs Metadata

Generate docs metadata before importing if the UI needs product-facing lifecycle fields:

```bash
scripts/extract-doc-metadata.py \
  --docs-repo /Users/grcai/Documents/GitHub/docs \
  --remote upstream
```

This writes:

```text
metadata/config-item-metadata.json
metadata/doc-metadata-candidates.json
```

`config-item-metadata.json` is imported automatically by `scripts/import-tidb.py`. `doc-metadata-candidates.json` is intentionally not imported; it is a review backlog for lower-confidence matches.

## Operating Model

Use this flow for future capture batches:

1. Capture and validate data locally.
2. Commit and push the sanitized repo data.
3. Extract docs metadata and review suspicious candidates when needed.
4. Import the repo data into TiDB Cloud Starter with `scripts/import-tidb.py --ssl --reset`.
5. Run the MVP query examples to verify that row counts and expected diffs look right.

The database can always be rebuilt from Git, so schema and importer changes should be committed together with the dataset format they support.
