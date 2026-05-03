# TiDB Config Capture Workflow

This document describes how to reproduce or extend the dataset in this repository. For the public repository overview and current data index, see `README.md`.

The MVP workflow is script-first:

1. `scripts/collect-version.sh` starts or reuses a TiUP playground cluster.
2. `scripts/collect-configs.py` collects SQL, HTTP, and TiFlash file snapshots.
3. `scripts/validate-capture.py` verifies JSON, counts, and checksums.
4. `scripts/sanitize-configs.py` can create a sanitized copy of an existing capture.
5. `scripts/sync-versions.py` syncs the release list from TiDB self-managed release notes.
6. `scripts/extract-release-note-events.py` extracts compatibility-change events from release notes.
7. `scripts/import-tidb.py` imports validated repo data into a TiDB Cloud Starter query layer.
8. `scripts/compare-configs.py` generates the repo-backed comparison read model.

## MVP Scope

The first MVP dataset uses `mvp-versions.json`:

```text
v6.5.12
v7.1.6
v7.5.0
v7.5.7
v8.1.0
v8.1.2
v8.5.0
v8.5.6
```

These are representative modern LTS versions. DMR versions and very old releases are intentionally excluded from the first pass.

Preview the batch commands:

```bash
scripts/collect-scope.sh --dry-run
```

Collect the full MVP scope:

```bash
scripts/collect-scope.sh --force
```

## Collect From An Existing Playground

If a local playground is already running on the default ports:

```bash
scripts/collect-version.sh v8.5.6 --reuse-running --force
```

By default, the output is sanitized and written to:

```text
v8.5.6/
```

The raw endpoint payloads are written under:

```text
v8.5.6/raw-sanitized/
```

Use `--no-sanitize` only for local debugging.

## Start Playground And Collect

```bash
scripts/collect-version.sh v8.5.6 --force
```

This starts:

```bash
tiup playground v8.5.6 --db 1 --pd 1 --kv 1 --tiflash 1 --without-monitor
```

The script waits for these endpoints:

- TiDB SQL: `127.0.0.1:4000`
- TiDB status: `127.0.0.1:10080/config`
- PD config: `127.0.0.1:2379/pd/api/v1/config`
- TiKV config: `127.0.0.1:20180/config?full=true`
- TiFlash config: `127.0.0.1:20292/config?full=true`

If the script starts the playground, it stops the playground after collection by default. Use `--reuse-running` to leave an existing cluster alone.

For version-to-version isolation, the managed playground flow also runs the official playground cleanup step:

```bash
tiup clean --all
```

This removes TiUP instantiated component data, but does not uninstall downloaded TiUP component packages.

## Output Structure

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

## Validate

```bash
scripts/validate-capture.py v8.5.6 --require-sanitized
```

## Import To Database

After the sanitized data is committed, import it into the derived TiDB database:

```bash
scripts/import-tidb.py --ssl --reset
```

See `DATABASE.md` for connection environment variables, schema details, and raw payload policy.

## Extract Release Note Events

Update the local docs refs:

```bash
git -C /Users/grcai/Documents/GitHub/docs fetch upstream --prune
```

Generate release-note events from the `release-8.5` docs branch:

```bash
scripts/extract-release-note-events.py \
  --docs-repo /Users/grcai/Documents/GitHub/docs \
  --release-notes-ref upstream/release-8.5
```

The generated events feed `Change note`, deprecation, removal, and replacement metadata. Diff status itself still comes only from captured config data.

## Compare Versions

Generate a screenshot-style comparison payload directly from the repository:

```bash
scripts/compare-configs.py --from-version v8.1.2 --to-version v8.5.6 --content-type system_variables
```

See `COMPARISON.md` for status semantics and read-model fields.

## Sync Versions

```bash
scripts/sync-versions.py
```

This writes `versions.json` from:

```text
https://docs.pingcap.com/releases/tidb-self-managed.md
```

DMR versions are recorded, but not marked as selected for capture by default.

## Notes

- System variables come from `INFORMATION_SCHEMA.VARIABLES_INFO` and are collected as one JSON object per row before being written to JSON/TSV. This avoids corrupting rows when a variable value contains embedded newlines.
- Normalized component values come from `SHOW CONFIG`.
- Raw TiKV and TiFlash payloads use `full=true`.
- TiFlash live config does not expose the complete C++ engine-store default catalog. Generate that catalog separately from TiFlash source and docs.
