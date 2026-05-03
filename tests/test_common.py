from __future__ import annotations

import json

from _common import default_cluster_tag
from _common import infer_cluster_tag
from _common import infer_value_type
from _common import sanitize_rules
from _common import sanitize_text


def test_default_cluster_tag_uses_version_digits() -> None:
    assert default_cluster_tag("v8.5.6") == "tidb-v856"
    assert default_cluster_tag("7.5.7") == "tidb-v757"


def test_infer_cluster_tag_prefers_manifest_version_when_tag_is_sanitized(tmp_path) -> None:
    (tmp_path / "manifest.json").write_text(
        json.dumps({"version": "v7.1.6", "cluster_tag": "${PLAYGROUND_TAG}"}),
        encoding="utf-8",
    )

    assert infer_cluster_tag(tmp_path) == "tidb-v716"


def test_infer_value_type_edges() -> None:
    assert infer_value_type("ON", "OFF") == "bool"
    assert infer_value_type("3", "-2") == "int"
    assert infer_value_type("3.14", ".5") == "float"
    assert infer_value_type("raft-kv", possible_values="raft-kv,partitioned-raft-kv") == "enum"
    assert infer_value_type(None, "-") is None


def test_sanitize_rules_replace_specific_path_before_home(monkeypatch) -> None:
    monkeypatch.setenv("HOME", "/Users/reviewer")
    monkeypatch.setenv("USER", "reviewer")
    rules = sanitize_rules("tidb-v757")
    text = "/Users/reviewer/.tiup/data/tidb-v757/tiflash-0 at 127.0.0.1"

    assert sanitize_text(text, rules) == "${TIUP_DATA_DIR}/tiflash-0 at ${LOCALHOST}"
