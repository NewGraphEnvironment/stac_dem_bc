"""Contract tests for the STAC Version Extension stamp.

The property that matters is not "a version can be written" — it is that the
version is never *wrong*. A version here means "the published catalogue is in
this state" (the NEWS.md convention), so a collection that has since grown items
must not still advertise the last release. Absent says "unversioned, go and
check"; a wrong version says "you already have this one", which is the shape of
the failure that let the API sit 38k items behind for a month.

So: stamping is release-only and explicit, clearing is what the monthly path
does, and neither may touch collection_patch()'s idempotence contract.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from collection_patch import (  # noqa: E402
    COLLECTION_ID,
    VERSION_EXT,
    collection_patch,
    version_clear,
    version_stamp,
)


def _collection(**extra):
    # The CURRENT id, not a literal. These tests are about version handling, so
    # the fixture should be a collection that is otherwise already patched --
    # a stale id here would make every collection_patch() call below report an
    # `id` change it is not testing for, and the assertions would go on passing
    # while describing a different world. The rename itself is covered in
    # tests/test_collection_identity.py.
    c = {"id": COLLECTION_ID, "type": "Collection", "links": []}
    c.update(extra)
    return c


# =============================================================================
# stamping
# =============================================================================

def test_version_stamp_sets_field_and_extension():
    c = version_stamp(_collection(), "1.1.0")
    assert c["version"] == "1.1.0"
    assert VERSION_EXT in c["stac_extensions"]


def test_version_stamp_on_collection_with_no_extensions_key():
    """The live collection has no `stac_extensions` key at all — not [], absent.

    Stamping must create it rather than assume it is there to append to.
    """
    c = _collection()
    assert "stac_extensions" not in c
    version_stamp(c, "1.0.0")
    assert c["stac_extensions"] == [VERSION_EXT]


def test_version_stamp_preserves_other_extensions():
    other = "https://stac-extensions.github.io/projection/v1.1.0/schema.json"
    c = version_stamp(_collection(stac_extensions=[other]), "1.0.0")
    assert c["stac_extensions"] == [other, VERSION_EXT]


def test_version_stamp_does_not_duplicate_the_extension():
    c = version_stamp(_collection(), "1.0.0")
    version_stamp(c, "1.0.1")
    assert c["stac_extensions"].count(VERSION_EXT) == 1
    assert c["version"] == "1.0.1"


def test_version_stamp_refuses_empty():
    """An empty version is the shape a failed `git describe` fallback produces."""
    with pytest.raises(ValueError, match="empty version"):
        version_stamp(_collection(), "")


# =============================================================================
# clearing — the "never wrong" half
# =============================================================================

def test_version_clear_removes_both_and_reports():
    c = version_stamp(_collection(), "1.0.0")
    assert version_clear(c) is True
    assert "version" not in c
    assert "stac_extensions" not in c


def test_version_clear_restores_the_original_key_set():
    """Clearing must leave a collection indistinguishable from a never-stamped one.

    Otherwise the monthly diff carries a spurious `stac_extensions: []`.
    """
    before = _collection()
    keys_before = sorted(before.keys())
    c = version_stamp(dict(before), "1.0.0")
    version_clear(c)
    assert sorted(c.keys()) == keys_before


def test_version_clear_keeps_other_extensions():
    other = "https://stac-extensions.github.io/projection/v1.1.0/schema.json"
    c = version_stamp(_collection(stac_extensions=[other]), "1.0.0")
    assert version_clear(c) is True
    assert c["stac_extensions"] == [other]


def test_version_clear_is_idempotent_and_reports_no_change():
    c = _collection()
    assert version_clear(c) is False


# =============================================================================
# the contract boundary — why --check does not start lying after a release
# =============================================================================

def test_collection_patch_ignores_version_entirely():
    """collection_patch() must neither add, remove, nor notice a version.

    If version handling lived inside it, `--check` would report "a patch is
    needed" after every release and the monthly run would silently re-stamp
    with whatever it computed.
    """
    stamped = version_stamp(_collection(), "1.0.0")
    _, changed = collection_patch(stamped)
    assert not any("version" in c for c in changed)
    assert stamped["version"] == "1.0.0"
    assert VERSION_EXT in stamped["stac_extensions"]


def test_collection_patch_second_run_reports_no_change_when_stamped():
    """The idempotence contract survives a stamped collection."""
    stamped = version_stamp(_collection(), "1.0.0")
    collection_patch(stamped)
    _, changed = collection_patch(stamped)
    assert changed == []


# =============================================================================
# CLI wiring
# =============================================================================

def test_cli_stamp_then_clear_round_trip(tmp_path):
    import subprocess

    path = tmp_path / "collection.json"
    path.write_text(json.dumps(_collection()))
    script = os.path.join(os.path.dirname(__file__), "..", "scripts", "collection_patch.py")

    subprocess.run([sys.executable, script, "--path", str(path), "--version", "9.9.9"],
                   check=True, capture_output=True)
    assert json.loads(path.read_text())["version"] == "9.9.9"

    subprocess.run([sys.executable, script, "--path", str(path), "--clear-version"],
                   check=True, capture_output=True)
    assert "version" not in json.loads(path.read_text())


def test_cli_rejects_stamp_and_clear_together(tmp_path):
    import subprocess

    path = tmp_path / "collection.json"
    path.write_text(json.dumps(_collection()))
    script = os.path.join(os.path.dirname(__file__), "..", "scripts", "collection_patch.py")

    r = subprocess.run(
        [sys.executable, script, "--path", str(path), "--version", "1.0.0", "--clear-version"],
        capture_output=True, text=True)
    assert r.returncode != 0
    assert "mutually exclusive" in r.stderr
