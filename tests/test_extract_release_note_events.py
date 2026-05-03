from __future__ import annotations

from _common import metadata_relpath
from conftest import ROOT
from conftest import load_script


extract_release_note_events = load_script("extract-release-note-events.py")


def test_default_release_notes_ref_tracks_max_scope_minor() -> None:
    assert extract_release_note_events.default_release_notes_ref(["v7.5.7", "v8.5.6"]) == "upstream/release-8.5"
    assert extract_release_note_events.default_release_notes_ref(["v9.1.0"]) == "upstream/release-9.1"


def test_metadata_relpath_avoids_absolute_local_paths() -> None:
    docs_repo = ROOT.parent / "docs"

    assert metadata_relpath(docs_repo, ROOT) == "../docs"
