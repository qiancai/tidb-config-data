from __future__ import annotations

from conftest import load_script


sync_versions = load_script("sync-versions.py")


def test_version_type_uses_lts_series_config() -> None:
    assert sync_versions.version_type("9.1.0", {"9.1"}) == "LTS"
    assert sync_versions.version_type("v9.1.1", {"9.1"}) == "LTS"
    assert sync_versions.version_type("9.2.0", {"9.1"}) == "GA"
    assert sync_versions.version_type("8.5.6-DMR", {"8.5"}) == "DMR"
