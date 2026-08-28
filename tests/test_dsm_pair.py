"""Contract tests for DEM/DSM pairing.

The pairing decides whether a tile gets a DSM asset or gets recorded as a gap,
so the tests below are the three known answers from issue #29 plus the two
failure modes that would make the pairing indistinguishable from a broken one:

  1. a `_dsm`-suffix mapsheet-year        -> full pairing, convention "suffix"
  2. an identical-basename mapsheet-year  -> full pairing, convention "identical"
  3. a .laz-only mapsheet-year            -> ZERO pairs and an explicit
                                             "no raster DSM" record
  4. an unfamiliar naming convention      -> still pairs on tile id + date, and
                                             is reported as convention "unknown"
  5. a failed listing                     -> raises, distinguishable from a
                                             genuinely empty product set

Fixtures are real listings taken from the objectstore on 2026-08-28, so the
tests exercise the actual filename shapes rather than idealised ones. They run
offline.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from dsm_pair import (  # noqa: E402
    NO_DSM_DIR,
    NO_RASTER_DSM,
    PAIRED,
    UNPAIRED,
    UNPARSEABLE,
    ListingError,
    keys_load,
    pairs_build,
    summarize,
)
from stac_utils import convention_classify, pair_key, tile_key_parse  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name: str) -> list[str]:
    with open(os.path.join(FIXTURES, name)) as fh:
        return [line.strip() for line in fh if line.strip()]


def groups_of(keys: list[str]) -> set[str]:
    """Mapsheet-years represented by a set of dsm/ keys, .laz included."""
    return {k.split("/dsm/")[0] for k in keys if "/dsm/" in k}


# ---------------------------------------------------------------------------
# 1. Suffix convention -- the 98.5% case
# ---------------------------------------------------------------------------

def test_suffix_convention_pairs_completely():
    dem = fixture("suffix_dem.txt")
    dsm = fixture("suffix_dsm.txt")
    result = pairs_build(dem, dsm, groups_of(dsm))
    summary = summarize(result)

    assert summary["status"][PAIRED] == len(dem)
    assert summary["convention"]["suffix"] == len(dem)
    assert all(r["dsm_key"] for r in result["rows"])
    assert result["dsm_unmatched"] == []


# ---------------------------------------------------------------------------
# 2. Identical basename -- the four 2022 mapsheet-years in block 082
# ---------------------------------------------------------------------------

def test_identical_basename_pairs_completely():
    dem = fixture("identical_dem.txt")
    dsm = fixture("identical_dsm.txt")
    result = pairs_build(dem, dsm, groups_of(dsm))
    summary = summarize(result)

    assert summary["status"][PAIRED] == len(dem)
    assert summary["convention"]["identical"] == len(dem)
    # The two names really are identical -- only the product directory differs.
    row = result["rows"][0]
    assert row["dem_key"].split("/")[-1] == row["dsm_key"].split("/")[-1]
    assert "/dem/" in row["dem_key"] and "/dsm/" in row["dsm_key"]


# ---------------------------------------------------------------------------
# 3. .laz-only delivery -- must be zero pairs AND an explicit record
# ---------------------------------------------------------------------------

def test_laz_only_group_yields_zero_pairs_and_an_explicit_record():
    """The guard that matters most: a .laz-only dsm/ must not read as success.

    These 1,211 tiles across 11 mapsheet-years have a dsm/ directory holding
    only point cloud. Reporting them as `no_raster_dsm` is a declared coverage
    gap; reporting them as paired-with-nothing, or omitting them, is the
    failure the issue was written to prevent.
    """
    dem = fixture("lazonly_dem.txt")
    dsm_dir_keys = fixture("lazonly_dsm_dir_keys.txt")
    raster_dsm = [k for k in dsm_dir_keys if k.lower().endswith(".tif")]
    assert raster_dsm == [], "fixture is not laz-only"

    result = pairs_build(dem, raster_dsm, groups_of(dsm_dir_keys))
    summary = summarize(result)

    assert summary["status"][PAIRED] == 0
    assert summary["status"][NO_RASTER_DSM] == len(dem)
    assert summary["groups_no_raster_dsm"] == ["082/082j/2018"]
    assert all(r["dsm_key"] == "" for r in result["rows"])


def test_no_dsm_directory_is_distinct_from_a_laz_only_one():
    """"No dsm/ delivered" and "dsm/ holds only .laz" are different facts."""
    dem = fixture("lazonly_dem.txt")
    result = pairs_build(dem, [], set())
    summary = summarize(result)

    assert summary["status"][NO_DSM_DIR] == len(dem)
    assert summary["status"].get(NO_RASTER_DSM, 0) == 0


# ---------------------------------------------------------------------------
# 4. An unfamiliar convention surfaces; it does not vanish
# ---------------------------------------------------------------------------

def test_unknown_convention_still_pairs_and_is_reported_as_unknown():
    """A future delivery using `_surface` must not lose its DSM silently.

    Matching is on tile id + acquisition date, so the pair is still found; the
    convention is recorded as `unknown` so it shows up for review. This is the
    inversion the issue asked for -- an assertion, not a lookup.
    """
    dem = ["094/094o/2026/dem/bc_094o056_2_1_4_xli1m_utm10_20260506_20260506.tif"]
    dsm = ["094/094o/2026/dsm/bc_094o056_2_1_4_xli1m_utm10_20260506_20260506_surface.tif"]
    result = pairs_build(dem, dsm, groups_of(dsm))

    row = result["rows"][0]
    assert row["status"] == PAIRED
    assert row["convention"] == "unknown"
    assert row["dsm_key"].endswith("_surface.tif")


def test_a_tile_with_no_dsm_in_a_populated_group_is_reported_unpaired():
    """A raster DSM exists in the group but not for this tile -- never dropped."""
    dem = fixture("suffix_dem.txt")
    dsm = fixture("suffix_dsm.txt")[:-1]  # withhold one DSM
    result = pairs_build(dem, dsm, groups_of(dsm))
    summary = summarize(result)

    assert summary["status"][UNPAIRED] == 1
    assert summary["status"][PAIRED] == len(dem) - 1
    unpaired = [r for r in result["rows"] if r["status"] == UNPAIRED]
    assert unpaired[0]["dem_key"], "the unpaired DEM must still be named"


# ---------------------------------------------------------------------------
# 5. A failed listing is an error, not an empty product set
# ---------------------------------------------------------------------------

def test_missing_listing_raises_rather_than_reading_as_no_dsm(tmp_path):
    with pytest.raises(ListingError):
        keys_load(str(tmp_path / "does_not_exist.txt"), "DSM")


def test_empty_listing_raises_rather_than_reading_as_no_dsm(tmp_path):
    empty = tmp_path / "urls_dsm.txt"
    empty.write_text("")
    with pytest.raises(ListingError):
        keys_load(str(empty), "DSM")


def test_a_populated_listing_loads(tmp_path):
    """The guard must not fire on the healthy case -- both known answers."""
    populated = tmp_path / "urls_dsm.txt"
    populated.write_text("a/b/dsm/x.tif\n\nb/c/dsm/y.tif\n")
    assert keys_load(str(populated), "DSM") == ["a/b/dsm/x.tif", "b/c/dsm/y.tif"]


# ---------------------------------------------------------------------------
# Conservation: nothing may vanish between input and output
# ---------------------------------------------------------------------------

def test_every_dem_key_appears_exactly_once_in_the_output():
    dem = fixture("suffix_dem.txt") + fixture("identical_dem.txt") + fixture("lazonly_dem.txt")
    dsm = fixture("suffix_dsm.txt") + fixture("identical_dsm.txt")
    dsm_dirs = groups_of(dsm) | groups_of(fixture("lazonly_dsm_dir_keys.txt"))
    result = pairs_build(dem, dsm, dsm_dirs)
    summary = summarize(result)

    assert len(result["rows"]) == len(dem)
    assert sum(summary["status"].values()) == len(dem)
    assert len({r["dem_key"] for r in result["rows"]}) == len(dem)


# ---------------------------------------------------------------------------
# The parser, on the filename shapes that actually exist in the bucket
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key,tile_id,dates,product", [
    # current generation: subdivided tile, two acquisition dates
    ("094/094o/2026/dem/bc_094o056_2_1_4_xli1m_utm10_20260506_20260506.tif",
     "094o056_2_1_4", ("20260506", "20260506"), None),
    ("094/094o/2026/dsm/bc_094o056_2_1_4_xli1m_utm10_20260506_20260506_dsm.tif",
     "094o056_2_1_4", ("20260506", "20260506"), "dsm"),
    # whole-mapsheet tile, single year
    ("082/082f/2022/dem/bc_082f005_xli1m_utm11_2022.tif",
     "082f005", ("2022",), None),
    # bcts generation, explicit _dem product token
    ("092/092e/2012/dem/bcts_092e095_3_3_4_x_2012_dem.tif",
     "092e095_3_3_4", ("2012",), "dem"),
    # oldest generation: no utm token, and 17603 is a project number not a date
    ("082/082e/2017/dem/bc_082e003_1_4_4_xl1m_17603.tif",
     "082e003_1_4_4", (), None),
])
def test_tile_key_parse_handles_every_real_filename_generation(key, tile_id, dates, product):
    parsed = tile_key_parse(key)
    assert parsed is not None
    assert parsed["tile_id"] == tile_id
    assert parsed["dates"] == dates
    assert parsed["product"] == product


def test_tile_key_parse_returns_none_rather_than_guessing():
    """albers10k2m carries no mapsheet tile id. Report it; do not invent one."""
    assert tile_key_parse("albers10k2m/_completed_dem/dem_055_154.tif") is None
    assert tile_key_parse("082/082f/2022/dsm/_$folder$") is None


def test_url_forms_agree_on_the_group():
    """urls_list.txt carries the single-slash `https:/` form from fs::path().

    If the full-URL and bare-key forms parsed to different groups, DEM and DSM
    would never match and every tile would report as a coverage gap.
    """
    tail = "082/082f/2022/dem/bc_082f005_xli1m_utm11_2022.tif"
    groups = {
        tile_key_parse(f"https:/nrs.objectstore.gov.bc.ca/gdwuts/{tail}")["group"],
        tile_key_parse(f"https://nrs.objectstore.gov.bc.ca/gdwuts/{tail}")["group"],
        tile_key_parse(tail)["group"],
    }
    assert groups == {"082/082f/2022"}


@pytest.mark.parametrize("dem,dsm,expected", [
    ("bc_x_2022", "bc_x_2022", "identical"),
    ("bc_x_2022", "bc_x_2022_dsm", "suffix"),
    ("bcts_x_2012_dem", "bcts_x_2012_dsm", "product_token"),
    ("bc_x_2022", "bc_x_2022_surface", "unknown"),
])
def test_convention_classify(dem, dsm, expected):
    assert convention_classify(dem, dsm) == expected


def test_unparseable_dem_is_reported_not_dropped():
    dem = ["albers10k2m/_completed_dem/dem_055_154.tif"]
    result = pairs_build(dem, [], set())
    assert result["rows"][0]["status"] == UNPARSEABLE
    assert result["rows"][0]["dem_key"] == "albers10k2m/_completed_dem/dem_055_154.tif"


# ---------------------------------------------------------------------------
# Regressions found by running the pairing against the whole bucket
# ---------------------------------------------------------------------------

def test_six_digit_mapsheet_codes_parse():
    """The bucket carries two tile-code widths, and only one was handled.

    `092h001212` (6 digits after the letter) is the older whole-mapsheet
    delivery form, 1,659 DEM tiles. Rejecting it put 901 of the 1,211 tiles
    stranded by .laz-only deliveries into `unparseable` instead of reporting
    them as the coverage gap they are.
    """
    parsed = tile_key_parse(
        "092/092h/2016/dem/bc_092h001212_xl2m_utm10_20160725_dem.tif"
    )
    assert parsed is not None
    assert parsed["tile_id"] == "092h001212"
    assert parsed["dates"] == ("20160725",)
    assert parsed["product"] == "dem"


def test_bcalb_projection_token_parses_without_a_utm_zone():
    """Some deliveries are BC Albers, so there is no utm token to key on."""
    parsed = tile_key_parse(
        "092/092h/2016/dem/bc_092h001212_xl1m_bcalb_20160725_dem.tif"
    )
    assert parsed is not None
    assert parsed["utm"] is None
    assert parsed["tile_id"] == "092h001212"


def test_a_reissued_dem_beside_its_original_is_reported_not_hidden():
    """083d/2019 ships `..._2019.tif` and `..._2019_1.tif` with one DSM.

    Both are the same footprint so both may carry the asset, but the sharing is
    reported -- the identical shape would also appear if the match key were too
    coarse to tell two real tiles apart, and those two cases must not look the
    same.
    """
    dem = [
        "083/083d/2019/dem/bc_083d014_xli1m_utm11_2019.tif",
        "083/083d/2019/dem/bc_083d014_xli1m_utm11_2019_1.tif",
    ]
    dsm = ["083/083d/2019/dsm/bc_083d014_xli1m_utm11_2019_dsm.tif"]
    result = pairs_build(dem, dsm, groups_of(dsm))

    assert all(r["status"] == PAIRED for r in result["rows"])
    assert len(result["dsm_shared"]) == 1
    assert len(next(iter(result["dsm_shared"].values()))) == 2
    conventions = {r["convention"] for r in result["rows"]}
    assert conventions == {"suffix", "unknown"}


def test_dsm_shared_is_empty_when_each_dem_has_its_own():
    """The other known answer: one DEM per DSM must not report as shared."""
    dsm = fixture("suffix_dsm.txt")
    result = pairs_build(fixture("suffix_dem.txt"), dsm, groups_of(dsm))
    assert result["dsm_shared"] == {}


def test_utm_zone_padding_does_not_prevent_pairing():
    """The bucket spells the zone both ways: 26,042 `utm09` against 8,049 `utm9`.

    092/092l/2023 carries a `utm09` DEM whose only DSM is spelled `utm9`.
    Matching on the raw token reported that tile as unpaired.
    """
    dem = ["092/092l/2023/dem/bc_092l061_2_1_2_xli1m_utm09_20231027_20231031.tif"]
    dsm = ["092/092l/2023/dsm/bc_092l061_2_1_2_xli1m_utm9_20231027_20231031_dsm.tif"]
    result = pairs_build(dem, dsm, groups_of(dsm))
    assert result["rows"][0]["status"] == PAIRED


def test_yymmdd_dates_separate_two_flights_of_one_tile():
    """`..._170529` and `..._170607` are the same tile flown twice.

    Without a YYMMDD branch in the date pattern both collapse to one match key,
    so one flight's DEM would take the other's DSM.
    """
    a = tile_key_parse("082/082l/2017/dem/bc_082l024_2_3_3_xl1m_utm11_170529.tif")
    b = tile_key_parse("082/082l/2017/dem/bc_082l024_2_3_3_xl1m_utm11_170607.tif")
    assert a["dates"] == ("170529",)
    assert b["dates"] == ("170607",)
    assert pair_key(a) != pair_key(b)


def test_a_five_digit_project_number_is_not_read_as_a_date():
    """17603 in bc_082e003_1_4_4_xl1m_17603 is a project number, not a date."""
    parsed = tile_key_parse("082/082e/2017/dem/bc_082e003_1_4_4_xl1m_17603.tif")
    assert parsed["dates"] == ()


def test_ambiguous_dsm_match_prefers_a_recognised_convention():
    """Two DSM spellings of one tile: pick the one that names a convention.

    An arbitrary pick would record `unknown` for tiles whose naming is in fact
    perfectly ordinary, burying the real unknowns in noise.
    """
    dem = ["092/092l/2023/dem/bc_092l061_xli1m_utm09_20231027.tif"]
    dsm = [
        "092/092l/2023/dsm/bc_092l061_xli1m_utm9_20231027_dsm.tif",
        "092/092l/2023/dsm/bc_092l061_xli1m_utm09_20231027_dsm.tif",
    ]
    result = pairs_build(dem, dsm, groups_of(dsm))
    row = result["rows"][0]
    assert row["convention"] == "suffix"
    assert row["dsm_key"].endswith("utm09_20231027_dsm.tif")


# ---------------------------------------------------------------------------
# The DEM/DSM lookup that item_create.py builds from the pairs csv
# ---------------------------------------------------------------------------

def test_only_paired_rows_yield_a_dsm_asset(tmp_path):
    """A declared gap must never become an asset pointing at nothing."""
    import csv as _csv
    from item_create import dsm_lookup_load
    from stac_utils import PATH_S3

    path = tmp_path / "pairs.csv"
    with open(path, "w", newline="") as fh:
        w = _csv.DictWriter(fh, fieldnames=["dem_key", "dsm_key", "group",
                                            "tile_id", "convention", "status"])
        w.writeheader()
        w.writerow({"dem_key": "a/b/dem/x.tif", "dsm_key": "a/b/dsm/x_dsm.tif",
                    "group": "a/b", "tile_id": "t", "convention": "suffix",
                    "status": PAIRED})
        for status in (NO_RASTER_DSM, NO_DSM_DIR, UNPAIRED, UNPARSEABLE):
            w.writerow({"dem_key": f"a/b/dem/{status}.tif", "dsm_key": "",
                        "group": "a/b", "tile_id": "t", "convention": "",
                        "status": status})

    lookup = dsm_lookup_load(str(path))
    assert lookup == {f"{PATH_S3}/a/b/dem/x.tif": f"{PATH_S3}/a/b/dsm/x_dsm.tif"}


def test_a_missing_pairs_file_yields_an_empty_lookup_not_a_crash(tmp_path):
    from item_create import dsm_lookup_load
    assert dsm_lookup_load(str(tmp_path / "absent.csv")) == {}


def test_cache_lookup_normalises_both_url_forms():
    """data/urls_list.txt uses `https:/`; every other URL source uses `https://`.

    Comparing them raw made an already-cached tile look new, which re-read it
    over the network AND appended a duplicate row to the cache on every run.
    Observed: a 6-URL build grew stac_geotiff_checks.csv by 6 rows.
    """
    from stac_utils import fix_url
    single = "https:/nrs.objectstore.gov.bc.ca/gdwuts/082/082f/2022/dem/x.tif"
    double = "https://nrs.objectstore.gov.bc.ca/gdwuts/082/082f/2022/dem/x.tif"
    assert fix_url(single) == fix_url(double) == double


def test_an_unparseable_dsm_keeps_its_group_marked_as_having_a_raster():
    """A DSM raster we cannot name must not turn its group into a coverage gap.

    `dsm_groups` was built only from parseable DSM keys, so a mapsheet-year
    whose rasters all failed to parse would drop out and its DEM tiles would
    report `no_raster_dsm` -- claiming the delivery published point cloud only,
    when in fact it shipped rasters we simply could not name. `unpaired` is the
    honest answer there.
    """
    dem = ["094/094o/2026/dem/bc_094o056_2_1_4_xli1m_utm10_20260506_20260506.tif"]
    dsm = ["094/094o/2026/dsm/surface_model_final.tif"]  # no mapsheet tile id
    result = pairs_build(dem, dsm, groups_of(dsm))

    assert len(result["dsm_unparseable"]) == 1
    assert result["rows"][0]["status"] == UNPAIRED, (
        "must be unpaired, not a point-cloud-only coverage gap"
    )


def test_unparseable_dsm_keys_appear_in_the_report():
    """Collected but never surfaced is the same as dropped silently."""
    from dsm_pair import report_render
    dem = ["094/094o/2026/dem/bc_094o056_2_1_4_xli1m_utm10_20260506_20260506.tif"]
    dsm = ["094/094o/2026/dsm/surface_model_final.tif"]
    result = pairs_build(dem, dsm, groups_of(dsm))
    report = report_render(summarize(result), result)

    assert "DSM keys with no parseable tile id (1)" in report
    assert "surface_model_final.tif" in report
