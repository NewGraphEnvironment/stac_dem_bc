"""Both item_create branches carry the same #2 fields for the same tile (AC2).

The cache branch builds from cached metadata; the rio_stac branch reads the
file. ~58k published items came from the second, so it is the one a monthly
pairing rebuild exercises. Offline: the rio_stac branch reads a small local
GeoTIFF named like a real tile.
"""

import json
import os
import sys

import numpy as np
import pytest
import rasterio
from shapely.geometry import shape

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.dirname(__file__))

import footprint as fp  # noqa: E402
import item_create  # noqa: E402
from test_footprint import TILE, UTM10, gap_mask  # noqa: E402


@pytest.fixture
def tile(tmp_path):
    t, mask, _, _ = gap_mask(0.4)
    a = np.where(mask, 500.0, -32767.0).astype("float32")
    path = str(tmp_path / TILE)
    with rasterio.open(path, "w", driver="GTiff", width=a.shape[1], height=a.shape[0], count=1,
                       dtype="float32", crs=UTM10, transform=t, nodata=-32767) as dst:
        dst.write(a, 1)
    row = {"url": path, "method": "download", **fp.footprint_from_mask(mask, t, UTM10, TILE),
           "checksum": "1220" + "cd" * 32, "size": "123"}
    with rasterio.open(path) as src:
        meta = {"is_geotiff": True, "is_cog": False, "epsg": 26910, "height": src.height,
                "width": src.width, "transform": json.dumps(list(src.transform)[:6]),
                "bounds": json.dumps(list(src.bounds))}
    return path, row, meta


def build(tmp_path, path, row, meta, sub):
    out = tmp_path / sub
    out.mkdir()
    r = item_create.process_item(path, "stac-elevation-bc", str(out), {path: meta}, {},
                                 {path: row})
    assert r is not None and r["footprint"]
    return json.loads(open(out / f"{r['id']}.json").read())


def test_cache_and_riostac_branches_carry_the_same_fields(tmp_path, tile):
    path, row, meta = tile
    a = build(tmp_path, path, row, meta, "cache")
    b = build(tmp_path, path, row, {**meta, "epsg": None}, "riostac")     # forces rio_stac
    assert a["geometry"] == b["geometry"] and a["bbox"] == b["bbox"]
    assert shape(a["properties"]["proj:geometry"]).equals_exact(
        shape(b["properties"]["proj:geometry"]), 0.01)
    for k in ("file:checksum", "file:size", "raster:bands"):
        assert a["assets"]["dem"][k] == b["assets"]["dem"][k], k
    assert set(a["stac_extensions"]) == set(b["stac_extensions"])


def test_no_footprint_row_builds_the_extent_and_says_so(tmp_path, tile):
    path, _, meta = tile
    out = tmp_path / "none"
    out.mkdir()
    r = item_create.process_item(path, "stac-elevation-bc", str(out), {path: meta}, {}, {})
    assert r is not None and r["footprint"] is False
    it = json.loads(open(out / f"{r['id']}.json").read())
    assert "file:checksum" not in it["assets"]["dem"]


# =============================================================================
# lidarbc: fields (#55, and the datetime_unknown rename)
# =============================================================================

import subprocess  # noqa: E402

import item_fields  # noqa: E402
import stac_utils  # noqa: E402

REPO = os.path.join(os.path.dirname(__file__), "..")
GDW = "https://nrs.objectstore.gov.bc.ca/gdwuts"


def body(href, props=None):
    return {"type": "Feature", "id": "x", "stac_extensions": [], "properties": props or {},
            "geometry": None, "bbox": None, "links": [],
            "assets": {"dem": {"href": href, "roles": ["data"]}}}


@pytest.mark.parametrize("href,want", [
    (f"{GDW}/092/092g/2016/dem/bc_092g036_1_1_1_x.tif", "092/092g/2016"),
    (f"{GDW}/102/102i/2012/dem/bcts_102i059_4_3_4_x_2012_dem.tif", "102/102i/2012"),
    (f"{GDW}/092/092l/2012/dem/bcts_092l032_2_1_3_x_2012_dem%20(2).tif", "092/092l/2012"),
    (f"{GDW}/albers10k2m/_completed_dem/dem_055_154.tif", None),
])
def test_delivery_comes_from_the_href(href, want):
    assert stac_utils.lidarbc_delivery(href) == want


def test_delivery_counts_match_55():
    urls = [u.strip() for u in open(os.path.join(REPO, "data", "urls_list.txt")) if u.strip()]
    got = [stac_utils.lidarbc_delivery(u) for u in urls]
    albers = sum(1 for u in urls if "/albers10k2m/" in u)
    assert sum(g is None for g in got) == albers            # only albers omit it
    assert all(g is not None for u, g in zip(urls, got) if "/albers10k2m/" not in u)
    assert albers > 2000 and len(urls) - albers > 100_000   # #55: 2,245 and 100,171 at filing


def test_apply_adds_delivery_and_renames_datetime_unknown():
    it = body(f"{GDW}/092/092g/2016/dem/bc_092g036_1_1_1_x.tif")
    assert item_fields.item_lidarbc_apply(it) == ["lidarbc"]
    assert it["properties"]["lidarbc:delivery"] == "092/092g/2016"
    assert stac_utils.LIDARBC_EXT in it["stac_extensions"]
    assert item_fields.item_lidarbc_apply(it) == []           # idempotent

    al = body(f"{GDW}/albers10k2m/_completed_dem/dem_055_154.tif", {"datetime_unknown": True})
    item_fields.item_lidarbc_apply(al)
    assert al["properties"] == {"lidarbc:datetime_unknown": True}
    assert stac_utils.LIDARBC_EXT in al["stac_extensions"]


def test_no_builder_writes_the_unprefixed_field():
    for f in ("item_create.py", "item_reprocess.py"):
        src = open(os.path.join(REPO, "scripts", f)).read()
        assert '"datetime_unknown"' not in src, f


def test_builders_write_the_prefixed_field(tmp_path, tile):
    path, _, meta = tile
    undated = str(tmp_path / "dem_055_154.tif")       # the albers shape: no date anywhere
    import shutil
    shutil.copy(path, undated)
    out = tmp_path / "o"
    out.mkdir()
    r = item_create.process_item(undated, "stac-elevation-bc", str(out), {undated: meta}, {}, {})
    it = json.loads(open(out / f"{r['id']}.json").read())
    assert it["properties"]["lidarbc:datetime_unknown"] is True
    assert "datetime_unknown" not in it["properties"]


def test_audit_passes_a_good_set_and_names_each_fault(tmp_path):
    good = body(f"{GDW}/092/092g/2016/dem/bc_092g036_1_1_1_x.tif")
    item_fields.item_lidarbc_apply(good)
    (tmp_path / "a.json").write_text(json.dumps(good))
    assert item_fields.audit_dir(str(tmp_path))["faults"] == {}

    bad = body(f"{GDW}/093/093l/2019/dem/bc_093l001_x.tif", {"datetime_unknown": True})
    (tmp_path / "b.json").write_text(json.dumps(bad))
    faults = item_fields.audit_dir(str(tmp_path))["faults"]
    assert set(faults) == {"missing lidarbc:delivery", "unprefixed datetime_unknown"}


def test_audit_cli_exits_nonzero_on_a_fault(tmp_path):
    (tmp_path / "b.json").write_text(json.dumps(body(f"{GDW}/093/093l/2019/dem/x.tif")))
    r = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "item_fields.py"),
                        "audit", "--dir", str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 1, r.stdout + r.stderr


def test_audit_refuses_an_empty_directory(tmp_path):
    with pytest.raises(SystemExit):
        item_fields.audit_dir(str(tmp_path))


def test_builders_keep_the_published_projection_shape(tmp_path, tile):
    # pystac 1.15's from_dict migrates projection v1.1.0 -> v2.0.0 and
    # proj:epsg -> proj:code unless told not to; the catalogue is v1.1.0.
    path, row, meta = tile
    for sub, m in (("c", meta), ("r", {**meta, "epsg": None})):
        it = build(tmp_path, path, row, m, sub)
        assert it["properties"]["proj:epsg"] == 26910 and "proj:code" not in it["properties"]
        assert "https://stac-extensions.github.io/projection/v1.1.0/schema.json" in it["stac_extensions"]
        assert not any("projection/v2" in e for e in it["stac_extensions"])


def test_hrefs_are_percent_encoded_at_construction(tmp_path, tile):
    # #25: published hrefs carry %20; a rebuild must not reintroduce a raw space.
    import shutil
    path, _, meta = tile
    spaced = str(tmp_path / "bc_092g036_1_1_1_xli1m_utm10_2024 (2).tif")
    shutil.copy(path, spaced)
    out = tmp_path / "s"
    out.mkdir()
    dsm = spaced.replace("bc_", "dsm_")
    r = item_create.process_item(spaced, "stac-elevation-bc", str(out), {spaced: meta}, {spaced: dsm}, {})
    it = json.loads(open(out / f"{r['id']}.json").read())
    for k in ("dem", "dsm"):
        assert " " not in it["assets"][k]["href"] and "%20" in it["assets"][k]["href"], k
