# Documentation Metadata Extraction

This repository uses docs metadata to enrich playground captures with product-facing fields that are not available from a freshly deployed cluster, such as descriptions, docs links, `New in` markers, deprecation notes, and replacements.

The workflow is script-first with a review fallback:

1. Read Markdown files from the local `pingcap/docs` repository by release branch.
2. Parse Markdown headings into item blocks instead of grepping whole files.
3. Apply high-confidence lifecycle rules automatically.
4. Write lower-confidence matches to a candidate review file.
5. Import the reviewed metadata into the TiDB query layer.

## Source Files

The extractor reads these docs files:

| Content type | Docs file |
|---|---|
| `system_variables` | `system-variables.md` |
| `tidb_config` | `tidb-configuration-file.md` |
| `tikv_config` | `tikv-configuration-file.md` |
| `pd_config` | `pd-configuration-file.md` |
| `tiflash_config` | `tiflash/tiflash-configuration.md` |

TiFlash `SHOW CONFIG` also exposes many TiKV proxy settings under the `raftstore-proxy.*` prefix. For those rows, the extractor derives TiFlash metadata from the matching TiKV docs item.

## Generate Metadata

Update the official docs refs first:

```bash
git -C /Users/grcai/Documents/GitHub/docs fetch upstream --prune
```

Then generate metadata for the MVP version scope:

```bash
scripts/extract-doc-metadata.py \
  --docs-repo /Users/grcai/Documents/GitHub/docs \
  --remote upstream
```

The extractor reads the release branches implied by `mvp-versions.json`, for example `upstream/release-6.5` and `upstream/release-8.5`.

## Outputs

```text
metadata/
  config-item-metadata.json
  doc-metadata-candidates.json
```

- `config-item-metadata.json` is the program-facing metadata file consumed by `scripts/compare-configs.py` and `scripts/import-tidb.py`.
- `doc-metadata-candidates.json` is the review backlog for lower-confidence matches, such as "not recommended", "might be deprecated", or experimental feature warnings.

## Automatic Rules

The extractor auto-applies high-confidence lifecycle metadata when a matched item block contains patterns such as:

- `Starting from vX.Y.Z, this variable is deprecated`
- `Since vX.Y.Z, this configuration item is deprecated`
- Heading text that explicitly contains `(Deprecated)`
- `replaced by` or `superseded by` with a linked/backticked replacement item

It does not auto-apply low-confidence language as deprecation:

- `might be deprecated in future`
- `will be deprecated in a future release`
- `experimental feature ... might be changed or removed`
- `not recommended`

Those matches remain in `doc-metadata-candidates.json` for manual review.
