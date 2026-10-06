"""Contract tests for scripts/register_manifest.py, and for the one contract this
repo still shares with stacs.

1. A URL from an unexpected host yields a mangled-but-plausible item id rather
   than an error (`url_to_item_id` slices by prefix length without checking the
   prefix). It must raise.
2. The item-link hrefs are ENCODED here (`stac_utils.encode_url_for_gdal`) and
   DECODED to ids in stacs (`stacs.catalogue.collection_item_links`). The decoder
   must stay the exact inverse of this encoder, or ids carrying spaces -- 90 of
   them, #25 -- read as missing and orphaned at once. Neither repo can see the
   other half, so the round trip is pinned here, where both are installed.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from register_manifest import item_ids_from_urls  # noqa: E402
from stac_utils import PATH_S3, PATH_S3_STAC, encode_url_for_gdal  # noqa: E402
from stacs.catalogue import collection_item_links  # noqa: E402


# =============================================================================
# item_ids_from_urls — must raise rather than mangle
# =============================================================================

def test_item_ids_from_urls_maps_real_urls():
    urls = [
        f"{PATH_S3}/082/082f/2022/dem/bc_082f037_xli1m_utm11_2022.tif",
        f"{PATH_S3}/094/094o/2026/dem/bc_094o056_2_1_4_xli1m_utm10_20260506_20260506.tif",
    ]
    assert item_ids_from_urls(urls) == [
        "082-082f-2022-dem-bc_082f037_xli1m_utm11_2022",
        "094-094o-2026-dem-bc_094o056_2_1_4_xli1m_utm10_20260506_20260506",
    ]


def test_item_ids_from_urls_skips_blank_lines():
    urls = ["", "  ", f"{PATH_S3}/082/082f/2022/dem/x.tif", "\n"]
    assert item_ids_from_urls(urls) == ["082-082f-2022-dem-x"]


def test_item_ids_from_urls_reads_the_lists_as_stored():
    """data/urls_list.txt carries `https:/` with ONE slash on every line (102,416
    of them, 2026-10-06), and the check below once refused all of them."""
    stored = f"{PATH_S3}/082/082f/2022/dem/x.tif".replace("https://", "https:/", 1)
    assert stored.startswith("https:/n")
    assert item_ids_from_urls([stored]) == ["082-082f-2022-dem-x"]


def test_item_ids_from_urls_raises_on_foreign_host():
    """The silent-mangle case. Same length prefix, different host.

    url_to_item_id would slice off len(PATH_S3) characters regardless and
    return a plausible-looking id that matches nothing.
    """
    foreign = "https://example.invalid/gdwutsXX/082/082f/2022/dem/x.tif"
    with pytest.raises(ValueError, match="not on the objectstore"):
        item_ids_from_urls([foreign])


def test_item_ids_from_urls_raises_on_non_geotiff():
    with pytest.raises(ValueError, match="not a GeoTIFF"):
        item_ids_from_urls([f"{PATH_S3}/082/082f/2022/pointcloud/x.laz"])


# =============================================================================
# Our encoder, stacs' decoder — the round trip across the package boundary
# =============================================================================

def _ids_from_hrefs(tmp_path, stems):
    """The ids stacs reads back from item links this repo's encoder wrote."""
    coll = tmp_path / "collection.json"
    coll.write_text(json.dumps({
        "id": "x",
        "links": [{"rel": "item",
                   "href": f"{PATH_S3_STAC}/{encode_url_for_gdal(s)}.json"}
                  for s in stems],
    }))
    return collection_item_links(coll)


def test_decoder_is_the_exact_inverse_of_the_encoder(tmp_path):
    """Decoding must mirror `encode_url_for_gdal`, which encodes spaces ONLY.

    A general unquote() is not the inverse: an id carrying a literal '%' is never
    encoded on the way out, so decoding every escape on the way back yields a
    DIFFERENT id -- permanently missing and orphaned at once.
    """
    stems = ["plain", "with space", "a (2)", "100%25", "5%_slope", "report%41"]
    links = _ids_from_hrefs(tmp_path, stems)
    assert [i for i, _ in links] == stems


def test_one_of_the_90_repaired_items_round_trips(tmp_path):
    stem = "082-082e-2018-dem-bc_082e003_xli1m_utm11_2018 (2)"
    [(item_id, href)] = _ids_from_hrefs(tmp_path, [stem])
    assert item_id == stem
    # The href stays encoded: a fetch URL rebuilt from the decoded id carries a raw
    # space, and an HTTP request cannot be formed from it -- that is #25.
    assert "%20" in href and " " not in href


def test_encoder_is_lossy_for_a_literal_percent_20(tmp_path):
    """A known, unfixable limitation -- asserted so it is a decision, not a bug.

    `encode_url_for_gdal` encodes spaces and nothing else, so a filename containing
    the literal characters "%20" encodes to itself and is then indistinguishable
    from an encoded space. No published id contains '%' at all.
    """
    assert encode_url_for_gdal("a%20b") == encode_url_for_gdal("a b") == "a%20b"
    [(item_id, _)] = _ids_from_hrefs(tmp_path, ["a%20b"])
    assert item_id == "a b"
