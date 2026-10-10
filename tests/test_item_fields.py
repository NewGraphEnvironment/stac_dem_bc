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
