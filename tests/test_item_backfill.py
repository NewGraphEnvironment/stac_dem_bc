"""Contract tests for the published-item backfill.

`item_edit` is the whole of the backfill's decision-making and it is pure, so it
is tested offline. The network parts (fetch, write, manifest) are exercised by
running the script against a handful of real published items.

The property that matters most is the one that keeps a re-run cheap and safe:
an item already carrying the right content must report NO change, so the run
does not re-upload ~91k unchanged objects.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from item_backfill import item_edit, published_item_ids  # noqa: E402

DEM = "https://nrs.objectstore.gov.bc.ca/gdwuts/082/082f/2022/dem/bc_082f005_xli1m_utm11_2022.tif"
DSM = "https://nrs.objectstore.gov.bc.ca/gdwuts/082/082f/2022/dsm/bc_082f005_xli1m_utm11_2022.tif"
COG = "image/tiff; application=geotiff; profile=cloud-optimized"
PLAIN = "image/tiff; application=geotiff"


def item(href=DEM, media_type=PLAIN, assets=None):
    return {"id": "x", "assets": assets if assets is not None else
            {"image": {"href": href, "type": media_type, "roles": ["data"]}}}


def test_a_paired_item_gains_a_dsm_asset():
    it = item()
    changed = item_edit(it, DSM)
    assert "asset:dsm" in changed
    assert it["assets"]["dsm"]["href"] == DSM
    assert it["assets"]["dsm"]["roles"] == ["data"]


def test_dsm_media_type_is_inherited_from_the_published_dem_asset():
    """The decision was to inherit COG status from the DEM, not re-measure it.

    The published item already IS the DEM's measured status, so inheriting from
    it needs no cache lookup and no second network read -- and cannot drift from
    what the catalog says.
    """
    it = item(media_type=COG)
    item_edit(it, DSM)
    assert it["assets"]["dsm"]["type"] == COG

    it2 = item(media_type=PLAIN)
    item_edit(it2, DSM)
    assert it2["assets"]["dsm"]["type"] == PLAIN


def test_raw_spaces_in_asset_hrefs_are_encoded():
    """#25's tail. A raw-space href cannot even be formed into an HTTP request."""
    spacey = "https://nrs.objectstore.gov.bc.ca/gdwuts/082/082e/2018/dem/bc_x_2018 (2).tif"
    it = item(href=spacey)
    changed = item_edit(it, None)
    assert changed == ["href:image"]
    assert " " not in it["assets"]["image"]["href"]
    assert "%20" in it["assets"]["image"]["href"]
    # Parentheses are legal URL sub-delims and resolve as-is; encoding spaces
    # alone matches what item_create.py does for new links, so legacy and new
    # items end up identically encoded rather than differently.
    assert "(2)" in it["assets"]["image"]["href"]


def test_an_already_correct_item_reports_no_change():
    """The property that keeps a re-run from re-uploading ~91k objects."""
    it = item(assets={
        "image": {"href": DEM, "type": PLAIN, "roles": ["data"]},
        "dsm": {"href": DSM, "type": PLAIN, "roles": ["data"]},
    })
    assert item_edit(it, DSM) == []


def test_an_unpaired_item_with_a_clean_href_is_untouched():
    it = item()
    before = json.dumps(it, sort_keys=True)
    assert item_edit(it, None) == []
    assert json.dumps(it, sort_keys=True) == before


def test_an_existing_dsm_asset_is_never_overwritten():
    """If a DSM is already attached, leave it. Re-pointing it is not this
    script's job, and doing it silently would hide a pairing change."""
    other = DSM.replace("bc_082f005", "bc_999z999")
    it = item(assets={
        "image": {"href": DEM, "type": PLAIN, "roles": ["data"]},
        "dsm": {"href": other, "type": PLAIN, "roles": ["data"]},
    })
    item_edit(it, DSM)
    assert it["assets"]["dsm"]["href"] == other


def test_both_edits_apply_together():
    spacey_dem = "https://nrs.objectstore.gov.bc.ca/gdwuts/a/dem/x 1.tif"
    spacey_dsm = "https://nrs.objectstore.gov.bc.ca/gdwuts/a/dsm/x 1_dsm.tif"
    it = item(href=spacey_dem)
    changed = item_edit(it, spacey_dsm)
    assert set(changed) == {"href:image", "asset:dsm"}
    assert "%20" in it["assets"]["image"]["href"]
    assert "%20" in it["assets"]["dsm"]["href"]
    assert " " not in it["assets"]["dsm"]["href"]


def test_published_ids_decode_the_percent_encoding_in_links(tmp_path):
    """Links are stored encoded; item ids are not. If these disagreed, the 90
    legacy items would be looked up under the wrong id and silently skipped."""
    coll = tmp_path / "collection.json"
    coll.write_text(json.dumps({"links": [
        {"rel": "item", "href": "https://s3/082-082e-2018-dem-bc_x_2018%20(2).json"},
        {"rel": "item", "href": "https://s3/082-082e-2022-dem-bc_y.json"},
        {"rel": "self", "href": "https://s3/collection.json"},
    ]}))
    ids = published_item_ids(str(coll))
    assert ids == {"082-082e-2018-dem-bc_x_2018 (2)", "082-082e-2022-dem-bc_y"}


@pytest.mark.parametrize("bad", [
    {"assets": {}},                       # no assets at all
    {"assets": {"image": {}}},            # asset with no href
])
def test_malformed_items_do_not_raise(bad):
    """A single odd item must not take down a 91k-item run."""
    item_edit(dict(bad), DSM)
