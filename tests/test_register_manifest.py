"""Contract tests for scripts/register_manifest.py.

The registration path has three failure modes that are silent by nature, and
these are the tests for them:

1. A URL from an unexpected host yields a mangled-but-plausible item id rather
   than an error (`url_to_item_id` slices by prefix length without checking the
   prefix). It must raise.
2. An item id is only recoverable from a published href by percent-DECODING it;
   90 items carry literal spaces and parentheses. Rebuilding a fetch URL from a
   decoded id produces a request that cannot be formed — that is #25's bug.
3. Verification by returned-count passes vacuously, because /search silently
   omits ids that do not exist. Only set equality, in both directions, works.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from register_manifest import (  # noqa: E402
    _href_to_id,
    collection_item_links,
    ids_diff,
    item_ids_from_urls,
    ndjson_write,
    search_body,
)
from stac_utils import PATH_S3  # noqa: E402


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
# href -> id — the percent-decoding contract (#25's 90 items)
# =============================================================================

def test_href_to_id_plain():
    href = "https://stac-dem-bc.s3.amazonaws.com/082-082e-2017-dem-bc_082e003_xl1m_17603.json"
    assert _href_to_id(href) == "082-082e-2017-dem-bc_082e003_xl1m_17603"


def test_href_to_id_percent_encoded_space_and_parens():
    """One of the 90 repaired items. The id carries a literal space."""
    href = ("https://stac-dem-bc.s3.amazonaws.com/"
            "082-082e-2018-dem-bc_082e003_xli1m_utm11_2018%20(2).json")
    assert _href_to_id(href) == "082-082e-2018-dem-bc_082e003_xli1m_utm11_2018 (2)"


def test_href_to_id_raises_on_non_json():
    with pytest.raises(ValueError, match="does not end in .json"):
        _href_to_id("https://stac-dem-bc.s3.amazonaws.com/some-item.tif")


def test_collection_item_links_keeps_href_encoded(tmp_path):
    """The id is decoded; the href is NOT.

    A fetch URL rebuilt from the decoded id carries a raw space, and an HTTP
    request cannot be formed from it — that is exactly #25.
    """
    coll = tmp_path / "collection.json"
    coll.write_text(json.dumps({
        "id": "stac-dem-bc",
        "links": [
            {"rel": "root", "href": "https://x/collection.json"},
            {"rel": "item", "href": "https://x/a%20(2).json"},
            {"rel": "item", "href": "https://x/b.json"},
        ],
    }))
    links = collection_item_links(coll)
    assert links == [("a (2)", "https://x/a%20(2).json"), ("b", "https://x/b.json")]
    assert "%20" in links[0][1]
    assert " " in links[0][0]


def test_collection_item_links_ignores_non_item_rels(tmp_path):
    coll = tmp_path / "collection.json"
    coll.write_text(json.dumps({
        "links": [
            {"rel": "self", "href": "https://x/collection.json"},
            {"rel": "parent", "href": "https://x/catalog.json"},
        ],
    }))
    assert collection_item_links(coll) == []


# =============================================================================
# ids_diff — both directions, and the vacuous-pass guard
# =============================================================================

def test_ids_diff_reports_both_directions():
    missing, orphaned = ids_diff(["a", "b", "c"], ["b", "c", "z"])
    assert missing == ["a"]
    assert orphaned == ["z"]


def test_ids_diff_empty_when_in_sync():
    missing, orphaned = ids_diff(["a", "b"], ["b", "a"])
    assert missing == []
    assert orphaned == []


def test_ids_diff_catches_a_shortfall_that_a_count_would_miss():
    """The vacuous-pass case, stated as a test.

    Both sides have 3 ids, so any check comparing LENGTHS passes. The sets are
    different, and that is the drift. This is why registration is never
    verified on a count.
    """
    published = ["a", "b", "c"]
    registered = ["a", "b", "zzz"]
    assert len(published) == len(registered)          # a count check passes here
    missing, orphaned = ids_diff(published, registered)
    assert missing == ["c"]                            # set equality does not
    assert orphaned == ["zzz"]


# =============================================================================
# ndjson_write
# =============================================================================

def _write_item(tmp_path, name, extra=None):
    doc = {"id": name, "type": "Feature", "collection": "stac-dem-bc",
           "assets": {"image": {"href": f"https://x/{name}.tif"}}}
    doc.update(extra or {})
    p = tmp_path / f"{name}.json"
    # deliberately pretty-printed, to prove compaction happens
    p.write_text(json.dumps(doc, indent=2))
    return p


def test_ndjson_write_one_line_per_item(tmp_path):
    paths = [_write_item(tmp_path, n) for n in ("a", "b", "c")]
    out = tmp_path / "items.ndjson"
    n = ndjson_write([str(p) for p in paths], out)
    assert n == 3
    lines = out.read_text().splitlines()
    assert len(lines) == 3
    for line in lines:
        doc = json.loads(line)
        assert {"id", "collection", "assets"} <= set(doc)


def test_ndjson_write_compacts_multiline_source(tmp_path):
    """A pretty-printed source must not straddle lines in the output."""
    p = _write_item(tmp_path, "a")
    assert "\n" in p.read_text()                       # source really is multi-line
    out = tmp_path / "items.ndjson"
    assert ndjson_write([str(p)], out) == 1
    assert len(out.read_text().splitlines()) == 1


def test_ndjson_write_skips_blank_input_lines(tmp_path):
    p = _write_item(tmp_path, "a")
    out = tmp_path / "items.ndjson"
    assert ndjson_write([str(p) + "\n", "", "\n"], out) == 1


def test_ndjson_write_handles_paths_with_spaces(tmp_path):
    """90 published ids carry a literal space, so their filenames do too."""
    doc = {"id": "a (2)", "type": "Feature", "collection": "stac-dem-bc"}
    p = tmp_path / "a (2).json"
    p.write_text(json.dumps(doc))
    out = tmp_path / "items.ndjson"
    assert ndjson_write([str(p)], out) == 1
    assert json.loads(out.read_text())["id"] == "a (2)"


def test_ndjson_write_raises_on_missing_file(tmp_path):
    """A silently-skipped input is a silently-unregistered item."""
    out = tmp_path / "items.ndjson"
    with pytest.raises(FileNotFoundError):
        ndjson_write([str(tmp_path / "nope.json")], out)


def test_ndjson_write_empty_input_yields_zero(tmp_path):
    """Zero must be reachable and reported, not conflated with success."""
    out = tmp_path / "items.ndjson"
    assert ndjson_write([], out) == 0
    assert out.read_text() == ""


# =============================================================================
# search_body — the API's default limit is 10, and an unscoped search answers
# about every collection on the endpoint
# =============================================================================

# Deliberately not either real collection id. search_body must carry through
# whatever it is handed; a fixture naming a real collection would let a
# hardcoded id pass this file.
COLL = "any-collection"


def test_search_body_always_carries_a_limit():
    """The bug this pins cost a full verification pass.

    POST /search without a `limit` returns the API's default of 10 features,
    whatever the length of the ids list. Measured against the live API: 600
    registered ids with no limit returned 10; with limit=600 it returned 600.
    A verify built on the unlimited body reports "590 of your 600 items are
    missing" about a catalogue that is entirely fine, and exits non-zero after
    the upsert has already succeeded.
    """
    body = search_body([f"id-{i}" for i in range(600)], COLL)
    assert body["limit"] == 600


def test_search_body_limit_matches_the_id_count_at_every_size():
    for n in (1, 9, 10, 11, 500):
        body = search_body([f"id-{i}" for i in range(n)], COLL)
        assert body["limit"] == n, f"limit must equal the id count at n={n}"


def test_search_body_limit_is_never_zero():
    """limit=0 would return nothing and read as 'everything is missing'."""
    assert search_body([], COLL)["limit"] >= 1


def test_search_body_requests_only_ids():
    body = search_body(["a"], COLL)
    assert body["fields"] == {"include": ["id"]}
    assert body["ids"] == ["a"]


def test_search_body_would_have_caught_the_shipped_bug():
    """A 3-item fixture cannot reach this failure, which is why it shipped.

    3 < 10, so the unlimited body returned all 3 and the only manual exercise
    on record passed. The assertion has to be on a list LONGER than the default
    limit or it proves nothing.
    """
    small = search_body(["a", "b", "c"], COLL)
    assert small["limit"] == 3
    big = search_body([f"id-{i}" for i in range(11)], COLL)
    assert big["limit"] == 11 and big["limit"] > 10


def test_search_body_scopes_to_the_collection():
    """Without `collections`, /search asks "is this id served ANYWHERE".

    That is a different question from the one every caller means, and it was
    invisible for as long as one collection existed on the endpoint. #34 puts
    two there by design, sharing all 102,460 ids — so an unscoped verification
    of stac-elevation-bc would pass green on stac-dem-bc's rows even if zero
    items registered into the new collection, immediately after the upsert it
    was meant to prove.
    """
    body = search_body(["a", "b"], COLL)
    assert body["collections"] == [COLL]


def test_search_body_carries_through_the_id_it_is_given():
    """A body that ignored its argument would pass the assertion above if the
    fixture happened to match a hardcoded default. Two distinct ids, checked."""
    assert search_body(["a"], "one")["collections"] == ["one"]
    assert search_body(["a"], "two")["collections"] == ["two"]


@pytest.mark.parametrize("bad", ["", None])
def test_search_body_refuses_an_empty_collection_id(bad):
    """Fail loudly rather than fall back to an unscoped search.

    An empty string is not the same as unset, and a `collections: [""]` body
    would match nothing — which reads as "every item is missing" rather than
    as a caller that forgot to pass the id.
    """
    with pytest.raises(ValueError, match="collection_id is required"):
        search_body(["a"], bad)


def test_href_to_id_is_the_exact_inverse_of_the_encoder():
    """Decoding must mirror `encode_url_for_gdal`, which encodes spaces ONLY.

    A general unquote() is not the inverse. An id carrying a literal '%' is
    never encoded on the way out, so decoding every escape on the way back
    yields a DIFFERENT id — one that is permanently missing and permanently
    orphaned at once, failing every --drift verification after a successful
    upsert. No such id exists today; this keeps it impossible rather than
    unlikely.
    """
    from stac_utils import encode_url_for_gdal

    for stem in ("plain", "with space", "a (2)", "100%25", "5%_slope"):
        href = "https://x/" + encode_url_for_gdal(stem) + ".json"
        assert _href_to_id(href) == stem, f"round trip broke for {stem!r}"


def test_encoder_is_lossy_for_a_literal_percent_20():
    """A known, unfixable limitation — asserted so it is a decision, not a bug.

    `encode_url_for_gdal` encodes spaces and nothing else, so a filename
    containing the literal characters "%20" encodes to itself and is then
    indistinguishable from an encoded space. No decoder can separate the two;
    the ambiguity is in the encoding. Recorded here rather than left to be
    rediscovered as a mysteriously missing item.

    Nothing in the published catalogue hits this (no id contains '%' at all),
    and it would require a source filename containing the literal text "%20".
    """
    from stac_utils import encode_url_for_gdal

    assert encode_url_for_gdal("a%20b") == "a%20b"          # encoder is a no-op
    assert encode_url_for_gdal("a b") == "a%20b"            # ...and so is "a b"
    assert _href_to_id("https://x/a%20b.json") == "a b"     # collapses to one


def test_href_to_id_does_not_decode_a_literal_percent_escape():
    """`%41` in a filename is the two characters, not 'A'."""
    assert _href_to_id("https://x/report%41.json") == "report%41"
