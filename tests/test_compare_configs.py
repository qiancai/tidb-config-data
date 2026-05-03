from __future__ import annotations

from conftest import load_script


compare_configs = load_script("compare-configs.py")


def test_collapse_config_rows_keeps_explicit_instance_order() -> None:
    rows = [
        {"Type": "tikv", "Instance": "127.0.0.1:20182", "Name": "raftstore.capacity", "Value": "2GiB"},
        {"Type": "tikv", "Instance": "127.0.0.1:20180", "Name": "raftstore.capacity", "Value": "1GiB"},
    ]

    collapsed = compare_configs.collapse_config_rows(rows)

    assert collapsed["raftstore.capacity"]["Instance"] == "127.0.0.1:20180,127.0.0.1:20182"
    assert collapsed["raftstore.capacity"]["Value"] == {
        "127.0.0.1:20180": "1GiB",
        "127.0.0.1:20182": "2GiB",
    }


def test_release_events_in_compare_range_excludes_source_version() -> None:
    events = [
        {"event_type": "modified", "version": "v8.5.0"},
        {"event_type": "modified", "version": "v8.5.1"},
        {"event_type": "deprecated", "version": "v8.5.6"},
        {"event_type": "new", "version": "v8.5.6"},
    ]

    selected = compare_configs.release_events_in_range(events, "v8.5.0", "v8.5.6")

    assert [event["version"] for event in selected] == ["v8.5.1", "v8.5.6"]
