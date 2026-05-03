from __future__ import annotations

from _common import NULL_MARKER
from conftest import load_script


collect_configs = load_script("collect-configs.py")


def test_tsv_to_rows_preserves_literal_null_string() -> None:
    rows = collect_configs.tsv_to_rows("name\tvalue\nliteral\tNULL\nactual\t\\N\n")

    assert rows == [
        {"name": "literal", "value": "NULL"},
        {"name": "actual", "value": None},
    ]


def test_rows_to_tsv_roundtrip_uses_unambiguous_null_marker() -> None:
    rows = [
        {"name": "literal", "value": "NULL"},
        {"name": "actual", "value": None},
    ]

    tsv = collect_configs.rows_to_tsv(rows, ["name", "value"])

    assert f"actual\t{NULL_MARKER}\n" in tsv
    assert collect_configs.tsv_to_rows(tsv) == rows
