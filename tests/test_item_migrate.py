"""Contract tests for the #34 migration of published items.

`item_migrate` is the whole of the migration's decision-making and it is pure,
so it is tested offline.

The property that matters most is the one nothing else in this repo can see:
a MIXED population. Item ids do not change during this rename, so set equality
reports IN SYNC over a half-migrated catalogue; item_register.sh routes each
item by its own `collection` field, so a stale body registers successfully;
item_validate.py sees legal STAC either way; and a count of assets cannot tell
{image, dsm} from {dem, dsm}.

Two fixture premises are asserted inline rather than assumed, because without
them these tests cannot reach the failure they exist to catch:

  the DEM href contains the literal path segment `/dem/`
  the collection LINK points at the bucket, which keeps the name stac-dem-bc

A fixture missing either would pass every assertion below while proving nothing
about the text-substitution failure — which is the one that would silently
break 102,460 download links.
"""

import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import collection_patch  # noqa: E402
import item_backfill  # noqa: E402
import item_migrate as migrate_mod  # noqa: E402
from item_migrate import ASSET_RENAMES, item_migrate  # noqa: E402
from register_manifest import audit_items, ndjson_write  # noqa: E402
from stac_utils import ASSET_DEM, ASSET_DSM, PATH_S3_STAC  # noqa: E402

NEW_ID = collection_patch.COLLECTION_ID
OLD_ID = "stac-dem-bc"

DEM = "https://nrs.objectstore.gov.bc.ca/gdwuts/082/082f/2022/dem/bc_082f005_xli1m_utm11_2022.tif"
DSM = "https://nrs.objectstore.gov.bc.ca/gdwuts/082/082f/2022/dsm/bc_082f005_xli1m_utm11_2022_dsm.tif"
COG = "image/tiff; application=geotiff; profile=cloud-optimized"
COLLECTION_LINK = f"{PATH_S3_STAC}/collection.json"


def published(with_dsm=False, item_id="082-082e-2017-dem-x"):
    """An item shaped exactly like a real published one, pre-migration."""
    assets = {"image": {"href": DEM, "type": COG, "roles": ["data"]}}
    if with_dsm:
        assets["dsm"] = {"href": DSM, "type": COG, "roles": ["data"],
                         "title": "Digital surface model"}
    return {
        "type": "Feature", "stac_version": "1.1.0", "id": item_id,
        "geometry": {"type": "Polygon", "coordinates": [[[0, 0]]]},
        "bbox": [0, 0, 1, 1],
        "properties": {"datetime": "2017-01-01T00:00:00Z", "proj:epsg": 26911},
        "assets": assets,
        "collection": OLD_ID,
        "links": [{"rel": "collection", "href": COLLECTION_LINK}],
    }


# =============================================================================
# fixture premises — assert them, or the tests below cannot fail
# =============================================================================

def test_the_fixture_can_reach_the_substitution_failure():
    it = published(with_dsm=True)
    assert "/dem/" in it["assets"]["image"]["href"], \
        "the DEM href must carry the /dem/ product segment"
    assert OLD_ID in it["links"][0]["href"], \
        "the collection link must be on the bucket, which keeps the old name"
    assert it["collection"] == OLD_ID
    assert "image" in it["assets"]


def test_the_two_names_really_are_different():
    """If the collection had kept its name these tests would be vacuous."""
    assert NEW_ID != OLD_ID
    assert ASSET_DEM != "image"


# =============================================================================
# the edit
# =============================================================================

def test_migrate_renames_the_asset_and_sets_the_collection():
    it = published()
    changed = item_migrate(it)
    assert ASSET_DEM in it["assets"]
    assert "image" not in it["assets"]
    assert it["collection"] == NEW_ID
    assert set(changed) == {f"asset:image->{ASSET_DEM}", "collection"}


def test_migrate_carries_the_asset_body_across_unchanged():
    it = published()
    before = copy.deepcopy(it["assets"]["image"])
    item_migrate(it)
    assert it["assets"][ASSET_DEM] == before


def test_migrate_does_not_touch_asset_hrefs():
    """The hrefs carry the literal segment /dem/ — the SOURCE product directory
    on the BC objectstore. A substitution over href text would corrupt every
    download link in the catalogue."""
    it = published(with_dsm=True)
    item_migrate(it)
    assert it["assets"][ASSET_DEM]["href"] == DEM
    assert "/dem/" in it["assets"][ASSET_DEM]["href"]
    assert it["assets"][ASSET_DSM]["href"] == DSM


def test_migrate_leaves_the_collection_link_on_the_old_bucket():
    """The BUCKET keeps its name. This link is not the collection id."""
    it = published()
    item_migrate(it)
    assert it["links"][0]["href"] == COLLECTION_LINK
    assert OLD_ID in it["links"][0]["href"]
    assert NEW_ID not in it["links"][0]["href"]


def test_migrate_preserves_asset_order():
    """`dem` must sit where `image` sat, so a byte diff of a published item
    shows one key changed rather than one removed and another appended."""
    it = published(with_dsm=True)
    assert list(it["assets"]) == ["image", "dsm"]
    item_migrate(it)
    assert list(it["assets"]) == [ASSET_DEM, ASSET_DSM]


def test_migrate_changes_nothing_else():
    it = published(with_dsm=True)
    before = copy.deepcopy(it)
    item_migrate(it)
    for key in ("type", "stac_version", "id", "geometry", "bbox", "properties",
                "links"):
        assert it[key] == before[key], f"{key} must not change"


def test_migrate_is_idempotent_and_reports_no_change():
    """What keeps a re-run from re-uploading 102k unchanged objects."""
    it = published(with_dsm=True)
    assert item_migrate(it)
    assert item_migrate(it) == []


def test_migrate_reports_only_what_it_actually_changed():
    """An item already on the new collection but still keyed `image`, and the
    reverse. Reporting a change that did not happen would put an unchanged
    object back on S3."""
    it = published()
    it["collection"] = NEW_ID
    assert item_migrate(it) == [f"asset:image->{ASSET_DEM}"]

    it2 = published()
    it2["assets"] = {ASSET_DEM: {"href": DEM}}
    assert item_migrate(it2) == ["collection"]


def test_migrate_refuses_an_item_carrying_both_keys():
    """A half-done previous run. There is no correct way to continue: keeping
    both publishes a duplicate asset, and picking one silently discards
    whatever the other run wrote."""
    it = published()
    it["assets"][ASSET_DEM] = {"href": DEM}
    with pytest.raises(ValueError, match="half-done"):
        item_migrate(it)


@pytest.mark.parametrize("bad", [
    {},                                  # nothing at all
    {"assets": {}},                      # no assets
    {"assets": {"image": {}}},           # asset with no href
    {"assets": None},                    # assets present but not a dict
    {"collection": None},                # no assets key at all
])
def test_malformed_items_do_not_raise(bad):
    """A single odd item must not take down a 102k-item run."""
    item_migrate(dict(bad))


def test_migrate_honours_an_explicit_collection_id():
    it = published()
    item_migrate(it, "some-other-collection")
    assert it["collection"] == "some-other-collection"


# =============================================================================
# one fact, one definition
# =============================================================================

def test_migrate_and_collection_patch_agree_on_the_id():
    """Derived twice is how the two ends of a rename disagree."""
    assert migrate_mod.COLLECTION_ID is collection_patch.COLLECTION_ID


def test_the_rename_targets_the_shared_asset_constant():
    assert ASSET_RENAMES == {"image": ASSET_DEM}


def test_the_migrate_manifest_is_not_the_backfill_manifest():
    """data/backfill_done.txt holds 98,040 ids against 102,460 published.

    Reading it here would present 98,040 items as already finished, migrate the
    remaining 4,420, and exit 0 — the mixed catalogue nothing else can see.
    """
    assert item_backfill.MANIFEST != migrate_mod.MANIFEST
    assert item_backfill.MIGRATION != migrate_mod.MIGRATION
    assert item_backfill.ERRORS_LOG != migrate_mod.ERRORS_LOG


# =============================================================================
# audit_items — the homogeneity gate
# =============================================================================

def _write(tmp_path, name, doc):
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps(doc))
    return str(p)


def test_audit_passes_a_fully_migrated_population(tmp_path):
    paths = []
    for i in range(3):
        it = published(with_dsm=bool(i % 2), item_id=f"x{i}")
        item_migrate(it)
        paths.append(_write(tmp_path, f"x{i}", it))
    r = audit_items(paths, NEW_ID, require_asset=ASSET_DEM, forbid_asset="image")
    assert r["checked"] == 3
    assert not r["wrong_collection"] and not r["missing_asset"]
    assert not r["forbidden_asset"] and not r["unreadable"]


def test_audit_catches_a_mixed_population(tmp_path):
    """THE failure. One stale item among many, which every other check misses.

    Two migrated, one not — the shape a reused manifest or an interrupted run
    produces, and the shape that silently splits the catalogue in two.
    """
    paths = []
    for i in range(2):
        it = published(item_id=f"good{i}")
        item_migrate(it)
        paths.append(_write(tmp_path, f"good{i}", it))
    paths.append(_write(tmp_path, "stale", published(item_id="stale")))

    r = audit_items(paths, NEW_ID, require_asset=ASSET_DEM, forbid_asset="image")
    assert r["checked"] == 3
    assert len(r["wrong_collection"]) == 1
    assert len(r["forbidden_asset"]) == 1
    assert len(r["missing_asset"]) == 1
    assert "stale" in r["wrong_collection"][0]


def test_audit_reports_paths_not_counts(tmp_path):
    """A count of offenders is no more use than a count of items — you have to
    be able to go and look at one."""
    p = _write(tmp_path, "stale", published(item_id="stale"))
    r = audit_items([p], NEW_ID, require_asset=ASSET_DEM, forbid_asset="image")
    assert r["wrong_collection"] == [p]


def test_audit_reports_an_unreadable_item_rather_than_skipping_it(tmp_path):
    bad = tmp_path / "broken.json"
    bad.write_text("{not json")
    r = audit_items([str(bad)], NEW_ID)
    assert r["checked"] == 0
    assert len(r["unreadable"]) == 1


def test_audit_over_nothing_reports_nothing_checked(tmp_path):
    """A loop over an empty set exits without complaint, which reads as
    'everything checked out'. The caller must be able to tell the difference,
    so `checked` is reported rather than inferred from an empty offender list."""
    r = audit_items([], NEW_ID, require_asset=ASSET_DEM)
    assert r["checked"] == 0
    assert not r["wrong_collection"]


# =============================================================================
# ndjson_write — the last checkpoint before pgstac
# =============================================================================

def test_ndjson_write_refuses_an_item_from_another_collection(tmp_path):
    """Items are routed by their own `collection` field — item_register.sh
    passes no collection id to pypgstac at all — so a stale body upserts into
    the PREVIOUS collection successfully, with no error anywhere."""
    good = published(item_id="good")
    item_migrate(good)
    paths = [_write(tmp_path, "good", good),
             _write(tmp_path, "stale", published(item_id="stale"))]
    out = str(tmp_path / "out.ndjson")
    with pytest.raises(RuntimeError, match=OLD_ID):
        ndjson_write(paths, out, expect_collection=NEW_ID)


def test_ndjson_write_without_the_guard_is_unchanged(tmp_path):
    """The guard is opt-in, so every existing caller keeps working."""
    paths = [_write(tmp_path, "stale", published(item_id="stale"))]
    out = str(tmp_path / "out.ndjson")
    assert ndjson_write(paths, out) == 1


def test_ndjson_write_passes_a_homogeneous_batch(tmp_path):
    paths = []
    for i in range(3):
        it = published(item_id=f"x{i}")
        item_migrate(it)
        paths.append(_write(tmp_path, f"x{i}", it))
    out = str(tmp_path / "out.ndjson")
    assert ndjson_write(paths, out, expect_collection=NEW_ID) == 3
    assert sum(1 for _ in open(out)) == 3


# =============================================================================
# the manifest — resumability that must not become stranding
# =============================================================================

from item_rewrite import MANIFEST_HEADER, manifest_load, manifest_open  # noqa: E402


def test_manifest_round_trips_its_own_ids(tmp_path):
    p = str(tmp_path / "m.txt")
    with manifest_open(p, "mine") as fh:
        fh.write("a\n")
        fh.write("b\n")
    assert manifest_load(p, "mine") == {"a", "b"}


def test_manifest_refuses_another_migrations_ledger(tmp_path):
    """The 4,420-of-102,460 failure, as a test."""
    p = str(tmp_path / "m.txt")
    with manifest_open(p, "31-dsm-backfill") as fh:
        fh.write("a\n")
    with pytest.raises(RuntimeError, match="belongs to migration"):
        manifest_load(p, "34-collection-rename")


def test_manifest_refuses_a_headerless_ledger(tmp_path):
    """Non-empty and unlabelled is the ambiguous case, so it fails loudly.

    Guessing "it is probably mine" is exactly the assumption that produces a
    run which skips 96% of the catalogue and exits 0.
    """
    p = tmp_path / "m.txt"
    p.write_text("a\nb\n")
    with pytest.raises(RuntimeError, match="no '# migration: ' header"):
        manifest_load(str(p), "mine")


def test_an_absent_or_empty_manifest_is_simply_empty(tmp_path):
    """A zero-byte file lists no ids, so nothing can be skipped because of it.

    Refusing it would lock the tool out of a path someone merely touched, while
    protecting against nothing — the dangerous case is a NON-empty file with no
    header, covered above.
    """
    assert manifest_load(str(tmp_path / "absent.txt"), "mine") == set()
    empty = tmp_path / "empty.txt"
    empty.write_text("")
    assert manifest_load(str(empty), "mine") == set()


def test_manifest_open_does_not_restamp_an_existing_ledger(tmp_path):
    """A second header line would be read back as an id."""
    p = str(tmp_path / "m.txt")
    manifest_open(p, "mine").close()
    manifest_open(p, "mine").close()
    body = open(p).read()
    assert body.count(MANIFEST_HEADER) == 1
    assert manifest_load(p, "mine") == set()


def test_manifest_load_survives_a_ledger_with_no_trailing_newline(tmp_path):
    """A final line without a newline is still an id, and dropping it would
    make the run redo one item -- harmless -- but dropping it from a
    COMPLETENESS comparison would fail a healthy run."""
    p = tmp_path / "m.txt"
    p.write_text(f"{MANIFEST_HEADER}mine\na\nb")
    assert manifest_load(str(p), "mine") == {"a", "b"}


def test_a_written_item_is_never_left_truncated(tmp_path, monkeypatch):
    """A partial write would be synced to S3 as an unparseable published item.

    It would also satisfy both resumability checks -- skip_already_staged and
    the completeness reconciliation treat "a file exists" as "this id is done"
    -- so a truncated file would be counted as migrated and never revisited.

    Simulated by failing mid-serialise, which is what a job timeout does to
    whatever item is in flight.
    """
    import item_rewrite

    class Boom(Exception):
        pass

    real_dump = item_rewrite.json.dump

    def exploding_dump(obj, fh, *a, **kw):
        fh.write('{"partial": ')      # bytes on disk, then death
        raise Boom("killed mid-write")

    monkeypatch.setattr(item_rewrite.json, "dump", exploding_dump)
    monkeypatch.setattr(item_rewrite, "item_fetch", lambda i: published(item_id=i))

    item_id, outcome = item_rewrite.process_one(
        "x", lambda i, d: item_migrate(d), str(tmp_path), attempts=1)

    assert outcome.startswith("error:")
    assert not (tmp_path / "x.json").exists(), \
        "a failed write must leave NO file, not a truncated one"
    assert list(tmp_path.iterdir()) == [], "and no leftover temp either"

    # And the same call succeeds once the writer works, so the test above is
    # about the failure and not about a path that never worked.
    monkeypatch.setattr(item_rewrite.json, "dump", real_dump)
    item_id, outcome = item_rewrite.process_one(
        "x", lambda i, d: item_migrate(d), str(tmp_path), attempts=1)
    assert outcome == "written"
    assert json.load(open(tmp_path / "x.json"))["collection"] == NEW_ID


def test_migrate_refuses_a_rename_map_that_collides():
    """Two old keys onto one new key would silently drop an asset.

    Not reachable with the shipped single-entry ASSET_RENAMES; asserted so a
    future migration cannot introduce it by editing a constant.
    """
    it = published(with_dsm=True)
    with pytest.raises(ValueError, match="maps two keys onto one"):
        item_migrate(it, NEW_ID, {"image": "x", "dsm": "x"})


# =============================================================================
# completeness — the two causes of "missing", which need opposite answers
# =============================================================================

def _run_migrate(tmp_path, monkeypatch, published_ids, written, errored,
                 extra_argv=()):
    """Drive item_migrate.main() with the fan-out replaced.

    The interaction under test only shows up in main(), between the error gate
    and the completeness reconciliation, and neither can be reached from a pure
    function. Replacing run_rewrite is what makes the two causes of a missing
    id settable independently.
    """
    import item_rewrite

    coll = tmp_path / "collection.json"
    coll.write_text(json.dumps({"id": NEW_ID, "links": [
        {"rel": "item", "href": f"{PATH_S3_STAC}/{i}.json"} for i in published_ids]}))
    manifest = tmp_path / "done.txt"

    def fake_run_rewrite(todo, edit, out_dir, manifest_fh, errors_fh, **kw):
        for i in written:
            manifest_fh.write(f"{i}\n")
        manifest_fh.flush()
        for i in errored:
            errors_fh.write(f"{i}\terror:boom\n")
        return ({"written": len(written), "unchanged": 0,
                 "error": len(errored)}, set(errored))

    monkeypatch.setattr(migrate_mod, "run_rewrite", fake_run_rewrite)
    argv = ["item_migrate.py", "--collection", str(coll),
            "--out-dir", str(tmp_path / "out"), "--manifest", str(manifest),
            "--errors-log", str(tmp_path / "err.txt"), *extra_argv]
    monkeypatch.setattr("sys.argv", argv)
    os.makedirs(tmp_path / "out", exist_ok=True)
    return migrate_mod.main()


def test_a_tolerated_failure_rate_still_publishes_what_completed(tmp_path, monkeypatch):
    """The loop this closes had no exit.

    An errored id is always in `todo`, so ANY error means `missing`, which meant
    exit 1 -- which in CI skips the sync, which discards the manifest, which
    throws away every completed item. At 102,460 items against a measured
    history of 34 and 2 transient failures per run, the migration could only
    ever finish on a run where none of 102,460 fetches failed three times.
    """
    ids = [f"id-{i}" for i in range(3000)]
    rc = _run_migrate(tmp_path, monkeypatch, ids, written=ids[:-2], errored=ids[-2:])
    assert rc == 0, "2 failures in 3000 is inside the 0.1% tolerance"


def test_an_intolerable_failure_rate_still_fails(tmp_path, monkeypatch):
    """The gate must still fail when something is actually broken -- otherwise
    the fix above has removed the guard rather than corrected it."""
    ids = [f"id-{i}" for i in range(100)]
    rc = _run_migrate(tmp_path, monkeypatch, ids, written=ids[:90], errored=ids[90:])
    assert rc == 1, "10% is far outside tolerance"


def test_an_item_never_attempted_is_always_fatal(tmp_path, monkeypatch):
    """The other cause of `missing`, and it needs the opposite answer.

    Nothing errored, so the error gate is silent -- yet an id in the published
    set reached neither the manifest nor the error log. That is a harness fault,
    not transient network noise, and no re-run will fix it by itself.
    """
    ids = [f"id-{i}" for i in range(10)]
    rc = _run_migrate(tmp_path, monkeypatch, ids, written=ids[:8], errored=[])
    assert rc == 1


def test_a_fully_complete_run_succeeds(tmp_path, monkeypatch):
    """The control. Without it the three above pass for a main() that always
    returns 1."""
    ids = [f"id-{i}" for i in range(10)]
    assert _run_migrate(tmp_path, monkeypatch, ids, written=ids, errored=[]) == 0
