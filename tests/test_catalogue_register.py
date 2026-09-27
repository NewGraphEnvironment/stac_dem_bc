"""End-to-end tests for scripts/catalogue_register.sh, with no network.

#42: the script takes STAC_COLLECTION / STAC_BUCKET_URL and is documented as
usable for any collection, but its pre-load audit required this repo's `dem`
asset on every item -- so another collection (stac-airphoto-bc, 10,100 items)
was refused in full. The asset half of the audit now follows the collection;
the collection-id half and the count always run.

The same tests pin the ORDER: the audit must refuse before the collection row
is upserted, not after. Before #42 it ran after `collection_register.sh`, so a
refused run had already written the collection.

How it runs offline:
  - collection.json and every item link are `file://` URLs, which `curl -fsSL`
    fetches like any other
  - `ssh` is a stub prepended to PATH. It answers the reachability probe
    (`ssh ... HOST true`) and records every other call -- each one is an
    attempted write -- then fails it, so nothing past the first write runs
  - STAC_HOST names an unresolvable host, so a stub that somehow failed to
    shadow the real ssh still reaches nothing
"""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from collection_patch import COLLECTION_ID  # noqa: E402
from item_migrate import ASSET_RENAMES  # noqa: E402
from stac_utils import ASSET_DEM, ASSET_DSM  # noqa: E402

FOREIGN_ID = "stac-airphoto-bc"
# The airphoto item shape, measured on stac_airphoto_bc's data/stac/.
FOREIGN_ASSETS = ("thumbnail", "flight_log")
OLD_KEY = next(iter(ASSET_RENAMES))

SSH_STUB = """#!/bin/bash
# The probe is `ssh -o ... HOST true`; anything else is a write.
last="${@: -1}"
if [ "$last" = "true" ]; then exit 0; fi
cat > /dev/null
echo write >> "$SSH_LOG"
exit 1
"""


def _asset(key):
    return {key: {"href": f"https://example.invalid/{key}", "type": "image/tiff"}}


def _item(item_id, collection, keys):
    assets = {}
    for k in keys:
        assets.update(_asset(k))
    return {"type": "Feature", "stac_version": "1.0.0", "id": item_id,
            "collection": collection, "geometry": None, "properties": {},
            "links": [], "assets": assets}


def _run(tmp_path, collection_id, items, env_extra=None, mode="--all",
         stac_collection=None):
    """Publish `items` under `collection_id` as file:// and run the script.

    Returns (CompletedProcess, number of attempted writes).
    """
    bucket = tmp_path / "bucket"
    bucket.mkdir()
    links = []
    for doc in items:
        p = bucket / f"{doc['id']}.json"
        p.write_text(json.dumps(doc))
        links.append({"rel": "item", "href": p.as_uri(),
                      "type": "application/json"})
    (bucket / "collection.json").write_text(json.dumps(
        {"type": "Collection", "id": collection_id, "stac_version": "1.0.0",
         "description": "fixture", "license": "proprietary",
         "extent": {}, "links": links}))

    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "ssh"
    stub.write_text(SSH_STUB)
    stub.chmod(0o755)
    log = tmp_path / "ssh.log"

    env = dict(os.environ)
    env.update({
        "PATH": f"{bindir}{os.pathsep}{env['PATH']}",
        "SSH_LOG": str(log),
        "STAC_HOST": "nobody@stub.invalid",
        "STAC_BUCKET_URL": bucket.as_uri(),
        # Unreachable, so the post-registration verify can never touch the
        # real API. Nothing here should get that far anyway.
        "STAC_API": "http://127.0.0.1:9",
        "FETCH_JOBS": "4",
    })
    for k in ("STAC_COLLECTION", "STAC_REQUIRE_ASSET", "STAC_FORBID_ASSET"):
        env.pop(k, None)
    if stac_collection is not None:
        env["STAC_COLLECTION"] = stac_collection
    env.update(env_extra or {})

    proc = subprocess.run(
        ["bash", "scripts/catalogue_register.sh", mode],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    writes = len(log.read_text().splitlines()) if log.exists() else 0
    return proc, writes


def _out(proc):
    return proc.stdout + proc.stderr


# =============================================================================
# A foreign collection registers
# =============================================================================

def test_foreign_collection_without_dem_passes_the_audit(tmp_path):
    """The #42 case: airphoto-shaped items carry no `dem`, and must not need one."""
    items = [_item(f"a{i}", FOREIGN_ID, FOREIGN_ASSETS) for i in range(3)]
    proc, writes = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID)
    out = _out(proc)
    assert "OK: every item agrees with its collection" in out, out
    assert f"lack asset {ASSET_DEM!r}" not in out
    # It went on to write: the collection upsert was attempted (and the stub
    # failed it, which is why the run exits non-zero here).
    assert writes == 1, out


def test_foreign_collection_says_asset_checks_are_off(tmp_path):
    """Off must be said out loud, or a foreign run reads as a checked pass."""
    items = [_item("a0", FOREIGN_ID, FOREIGN_ASSETS)]
    proc, _ = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID)
    assert "asset audit: none" in _out(proc), _out(proc)


def test_foreign_collection_still_audits_homogeneity(tmp_path):
    """Scoping the asset checks must not scope away the collection-id check."""
    items = [_item("a0", FOREIGN_ID, FOREIGN_ASSETS),
             _item("a1", "some-other-collection", FOREIGN_ASSETS)]
    proc, writes = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID)
    out = _out(proc)
    assert proc.returncode != 0
    assert "name another collection" in out, out
    assert writes == 0, "refused after a write -- the audit must run first"


def test_foreign_collection_can_opt_in_to_an_asset_requirement(tmp_path):
    items = [_item("a0", FOREIGN_ID, FOREIGN_ASSETS),
             _item("a1", FOREIGN_ID, ("flight_log",))]
    proc, writes = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID,
                        env_extra={"STAC_REQUIRE_ASSET": "thumbnail"})
    out = _out(proc)
    assert proc.returncode != 0
    assert "lack asset 'thumbnail'" in out, out
    assert writes == 0, "refused after a write -- the audit must run first"


def test_foreign_collection_can_opt_in_to_a_forbidden_asset(tmp_path):
    items = [_item("a0", FOREIGN_ID, FOREIGN_ASSETS),
             _item("a1", FOREIGN_ID, ("thumbnail", "legacy"))]
    proc, writes = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID,
                        env_extra={"STAC_FORBID_ASSET": "legacy"})
    out = _out(proc)
    assert proc.returncode != 0
    assert "retired asset key" in out, out
    assert writes == 0


# =============================================================================
# This repo's collection keeps the #34 guard
# =============================================================================

def test_own_collection_passes_with_the_current_keys(tmp_path):
    """Control for the two refusals below: a good elevation item gets through."""
    items = [_item("d0", COLLECTION_ID, (ASSET_DEM, ASSET_DSM)),
             _item("d1", COLLECTION_ID, (ASSET_DEM,))]
    proc, writes = _run(tmp_path, COLLECTION_ID, items)
    out = _out(proc)
    assert "OK: every item agrees with its collection" in out, out
    assert writes == 1, out


def test_own_collection_still_requires_dem(tmp_path):
    items = [_item("d0", COLLECTION_ID, (ASSET_DEM,)),
             _item("d1", COLLECTION_ID, (ASSET_DSM,))]
    proc, writes = _run(tmp_path, COLLECTION_ID, items)
    out = _out(proc)
    assert proc.returncode != 0
    assert f"lack asset {ASSET_DEM!r}" in out, out
    assert writes == 0, "refused after a write -- the audit must run first"


def test_own_collection_still_forbids_the_retired_key(tmp_path):
    """Half a rename: right collection, retired key beside the new one."""
    items = [_item("d0", COLLECTION_ID, (ASSET_DEM,)),
             _item("d1", COLLECTION_ID, (ASSET_DEM, OLD_KEY))]
    proc, writes = _run(tmp_path, COLLECTION_ID, items)
    out = _out(proc)
    assert proc.returncode != 0
    assert "retired asset key" in out, out
    assert writes == 0


def test_own_collection_named_explicitly_is_still_own(tmp_path):
    """STAC_COLLECTION set to this repo's id must not read as foreign."""
    items = [_item("d0", COLLECTION_ID, (ASSET_DSM,))]
    proc, writes = _run(tmp_path, COLLECTION_ID, items,
                        stac_collection=COLLECTION_ID)
    assert proc.returncode != 0
    assert f"lack asset {ASSET_DEM!r}" in _out(proc), _out(proc)
    assert writes == 0


@pytest.mark.parametrize("var", ["STAC_REQUIRE_ASSET", "STAC_FORBID_ASSET"])
def test_own_collection_refuses_an_asset_override(tmp_path, var):
    """The elevation rules come from the modules; env must not loosen them."""
    items = [_item("d0", COLLECTION_ID, (ASSET_DEM,))]
    proc, writes = _run(tmp_path, COLLECTION_ID, items,
                        env_extra={var: "anything"})
    out = _out(proc)
    assert proc.returncode != 0
    assert var in out, out
    # Refused at startup, before the fetch or anything else.
    assert "fetching" not in proc.stdout
    assert writes == 0
