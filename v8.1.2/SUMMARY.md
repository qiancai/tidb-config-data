# TiDB v8.1.2 Configuration Capture

Captured from a local TiUP playground cluster.

## Layout

- `manifest.json`: capture metadata, source endpoints, counts, and file hashes.
- `SHA256SUMS`: checksums for captured files.
- `normalized/`: flattened data for comparison and database import.
- `raw-sanitized/`: source payloads and TiFlash config file snapshots.

## Counts

- System variables: 1142
- SHOW CONFIG total: 1806
- TiDB config rows: 203
- TiKV config rows: 705
- PD config rows: 186
- TiFlash config rows: 712

## TiFlash Note

TiFlash `/config?full=true` combines `raftstore-proxy` config and the engine-store config file content.
It does not expose the complete C++ engine-store default catalog. Generate that catalog from TiFlash
source and docs separately.
