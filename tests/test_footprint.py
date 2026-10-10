"""Contract tests for tile footprints (#2).

A footprint is the part of a tile that holds data: for a BCGS 1:2,500 tile, its
grid cell minus the no-data inside it; for any other tile, its valid data inside
the raster extent. Everything here is offline. The grids are synthetic, built on
a real cell so the geometry is the same shape the objectstore delivers: a UTM
rectangle around a lat/lon cell, with ~22 m of data beyond the cell on every
side (measured, planning/archive .. findings, Phase 1).
"""

import csv
import json
import os
import sys

import numpy as np
import pytest
from affine import Affine
from rasterio.warp import transform as warp_transform
from shapely import wkt as shapely_wkt
from shapely.geometry import box, mapping, shape

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import footprint as fp  # noqa: E402

REPO = os.path.join(os.path.dirname(__file__), "..")
UTM10 = "EPSG:26910"
TILE = "bc_092g036_1_1_1_xli1m_utm10_20240101_20240101.tif"


# =============================================================================
# The cell, from the tile id
# =============================================================================

def test_cell_hand_checked():
    # 092 -> east -120, south 48; g -> lon -124..-122, lat 49..50;
    # 036 from the SW corner -> row 3, col 5 -> lon -123.0..-122.8, lat 49.3..49.4;
    # quadrants 1,1,1 -> the SW quarter three times.
    c = fp.bcgs_cell(TILE)
    assert c.bounds == pytest.approx((-123.0, 49.3, -122.975, 49.3125))


@pytest.mark.parametrize("name", [
    "bc_092g036_1_1_1_xli1m_utm10_20240101.tif",       # underscore form
    "bc_092g036111_xl2m_utm10_20160531_dem.tif",       # concatenated form (2016)
    "bcts_092g036_1_1_1_x_2012_dem.tif",               # BC Timber Sales prefix
])
def test_every_id_form_decodes_to_the_same_cell(name):
    assert fp.bcgs_cell(name).equals(fp.bcgs_cell(TILE))


@pytest.mark.parametrize("name", [
    "bc_082e002_xli1m_utm11_2018.tif",   # no quadrants: NOT a full 1:20k cell
    "dem_055_154.tif",                   # albers10k2m
    "bc_092g036_5_1_1_xli1m.tif",        # quadrant out of range
    "bc_092q036_1_1_1_xli1m.tif",        # letter out of range
])
def test_non_bcgs_ids_have_no_cell(name):
    assert fp.bcgs_cell(name) is None


def test_quadrants_count_from_the_south():
    # Found by measurement: assuming a NW origin mirrored every cell about its
    # letter block's mid-latitude. 1=SW, 2=SE, 3=NW, 4=NE.
    sw, se, nw, ne = (fp.bcgs_cell(f"bc_092g036_1_1_{q}_x.tif") for q in "1234")
    assert sw.bounds[1] == se.bounds[1] < nw.bounds[1] == ne.bounds[1]
    assert sw.bounds[0] == nw.bounds[0] < se.bounds[0] == ne.bounds[0]


def test_every_cached_tile_contains_its_cell():
    """Zero-I/O over the whole cached population, not a sample.

    data/stac_geotiff_checks.csv holds the raster bounds of ~44k tiles. Each
    BCGS tile's cell must lie inside its raster, with the measured ~13-17 m pad
    (the 2 m tiles are flush, so the floor is a small negative).
    """
    path = os.path.join(REPO, "data", "stac_geotiff_checks.csv")
    by_crs = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            if not (r["bounds"] and r["epsg"]):
                continue
            c = fp.bcgs_cell(r["url"].rsplit("/", 1)[1])
            if c is None:
                continue
            by_crs.setdefault(int(float(r["epsg"])), []).append((c.bounds, json.loads(r["bounds"])))
    n = sum(len(v) for v in by_crs.values())
    assert n > 40_000, f"only {n} cached BCGS tiles - population check is not a population"
    worst = np.inf
    outside = []
    for epsg, rows in by_crs.items():
        cells = np.array([c for c, _ in rows])
        rast = np.array([b for _, b in rows])
        # all four corners of each cell, in the raster's CRS
        xs = np.concatenate([cells[:, 0], cells[:, 2], cells[:, 2], cells[:, 0]])
        ys = np.concatenate([cells[:, 1], cells[:, 1], cells[:, 3], cells[:, 3]])
        tx, ty = warp_transform(fp.CELL_CRS, f"EPSG:{epsg}", xs, ys)
        tx = np.array(tx).reshape(4, -1)
        ty = np.array(ty).reshape(4, -1)
        pad = np.minimum.reduce([
            tx.min(0) - rast[:, 0], ty.min(0) - rast[:, 1],
            rast[:, 2] - tx.max(0), rast[:, 3] - ty.max(0)])
        far = pad < -fp.CRS_SLACK_M
        outside += [epsg] * int(far.sum())
        if (~far).any():
            worst = min(worst, pad[~far].min())
    # Measured worst 2026-10-10: -1.25 m, on the flush 2 m tiles.
    assert worst > -2.0, f"a cell extends {-worst:.1f} m outside its raster"
    # The only cells outside their rasters are the two zone-11 tiles that
    # declare UTM zone 14 (EPSG:6657); footprint_from_mask refuses those.
    assert outside == [6657, 6657], outside


def test_a_tile_declaring_the_wrong_utm_zone_is_refused():
    t, shape_ = grid(fp.bcgs_cell(TILE))      # built in zone 10
    with pytest.raises(fp.FootprintCrsMismatch):
        fp.footprint_from_mask(np.ones(shape_, bool), t, "EPSG:26911", TILE)


# =============================================================================
# The footprint, from a validity mask
# =============================================================================

def grid(cell_ll, pad=22.0, res=1.0, crs=UTM10):
    """A north-up grid covering `cell_ll` (lon/lat) plus `pad` metres."""
    xs, ys = warp_transform(fp.CELL_CRS, crs, *zip(*cell_ll.exterior.coords))
    x0, x1 = min(xs) - pad, max(xs) + pad
    y0, y1 = min(ys) - pad, max(ys) + pad
    w, h = int(np.ceil((x1 - x0) / res)), int(np.ceil((y1 - y0) / res))
    return Affine(res, 0, x0, 0, -res, y1), (h, w)


def cell_native(name=TILE, crs=UTM10):
    return fp.cell_in_crs(fp.bcgs_cell(name), crs)


def compute(mask, transform, name=TILE, crs=UTM10):
    return fp.footprint_from_mask(mask, transform, crs, name)


def test_a_full_tile_is_its_cell_and_stores_no_geometry():
    t, shape_ = grid(fp.bcgs_cell(TILE))
    r = compute(np.ones(shape_, bool), t)
    assert r["footprint_wkt"] == ""          # empty means "the cell"
    assert r["valid_percent"] == 100.0


def test_the_real_shape_a_utm_rectangle_with_nodata_corners_is_still_the_cell():
    # What every delivered tile looks like: data fills the cell plus the pad,
    # and the rectangle's corners beyond that are nodata.
    t, shape_ = grid(fp.bcgs_cell(TILE))
    data = cell_native().buffer(22.0)
    mask = fp.rasterize(data, t, shape_)
    r = compute(mask, t)
    assert r["footprint_wkt"] == ""
    assert r["valid_percent"] < 100.0


def gap_mask(frac=0.4):
    """A full tile with the western `frac` of the cell empty."""
    t, shape_ = grid(fp.bcgs_cell(TILE))
    mask = np.ones(shape_, bool)
    c = cell_native()
    x_cut = c.bounds[0] + frac * (c.bounds[2] - c.bounds[0])
    cols = int((x_cut - t.c) / t.a)
    mask[:, :cols] = False
    return t, mask, c, x_cut


def test_a_gap_inside_the_cell_is_excluded():
    t, mask, c, x_cut = gap_mask(0.4)
    r = compute(mask, t)
    g = fp.geom_native(r["footprint_wkt"], UTM10)
    assert g.area / c.area == pytest.approx(0.6, abs=0.02)
    assert g.bounds[0] >= x_cut - 3 * t.a      # outward bias is bounded
    assert g.within(c.buffer(0.5))             # never past the cell


def diagonal_mask():
    """A full tile whose data ends on a ragged diagonal - the edge simplify cuts into."""
    t, shape_ = grid(fp.bcgs_cell(TILE))
    rng = np.random.default_rng(1)
    h, w = shape_
    rows = np.arange(h)[:, None]
    cols = np.arange(w)[None, :]
    edge = (cols * h / w + rng.integers(-3, 4, (1, w)))
    return t, rows < edge


@pytest.mark.parametrize("make", [lambda: gap_mask(0.37)[:2], diagonal_mask])
def test_data_is_never_outside_the_footprint(make):
    # Simplification must move edges outward, not inward: a footprint that
    # drops real data is a false negative in every spatial search.
    t, mask = make()
    c = cell_native()
    r = compute(mask, t)
    g = fp.geom_native(r["footprint_wkt"], UTM10)
    data_in_cell = fp.polygonize(mask, t).intersection(c)
    assert data_in_cell.difference(g.buffer(0.01)).area == pytest.approx(0, abs=1.0)


def test_two_islands_give_a_multipolygon():
    t, shape_ = grid(fp.bcgs_cell(TILE))
    mask = np.zeros(shape_, bool)
    h, w = shape_
    mask[100:500, 100:500] = True
    mask[h - 500:h - 100, w - 500:w - 100] = True
    r = compute(mask, t)
    g = shapely_wkt.loads(r["footprint_wkt"])
    assert g.geom_type == "MultiPolygon" and len(g.geoms) == 2


def test_specks_are_dropped_and_small_holes_filled():
    # Both inside the cell, and both bigger than the outward buffer closes on
    # its own, so only the hygiene step can remove them.
    t, shape_ = grid(fp.bcgs_cell(TILE))
    mask = np.zeros(shape_, bool)
    h, w = shape_
    mask[200:h - 400, 200:w - 200] = True       # one big block
    mask[300:320, 300:320] = False              # 400 m2 hole
    mask[h - 200:h - 180, w // 2:w // 2 + 20] = True   # 400 m2 speck, 180 m from the block
    g = shapely_wkt.loads(compute(mask, t)["footprint_wkt"])
    assert g.geom_type == "Polygon"
    assert len(g.interiors) == 0


def test_a_large_hole_is_kept():
    t, shape_ = grid(fp.bcgs_cell(TILE))
    mask = np.zeros(shape_, bool)
    h, w = shape_
    mask[100:h - 100, 100:w - 100] = True
    mask[400:500, 400:500] = False              # 1 ha hole: a real gap
    g = shapely_wkt.loads(compute(mask, t)["footprint_wkt"])
    assert len(g.interiors) == 1


def test_out_of_range_values_are_invalid():
    a = np.full((3, 3), 500.0, dtype="float32")
    a[0, 0] = -3.4e38        # undeclared LidarBC nodata
    a[0, 1] = -32767         # declared
    a[0, 2] = np.nan
    a[1, 0] = 5001
    a[1, 1] = -101
    m = fp.valid_mask(a, nodata=-32767)
    assert m.tolist() == [[False, False, False], [False, False, True], [True, True, True]]


def test_all_nodata_refuses():
    t, shape_ = grid(fp.bcgs_cell(TILE))
    with pytest.raises(fp.FootprintEmpty):
        compute(np.zeros(shape_, bool), t)


@pytest.mark.parametrize("frac", [0.13, 0.4, 0.71])
def test_acceptance_valid_ccw_capped_rounded(frac):
    t, mask, c, _ = gap_mask(frac)
    r = compute(mask, t)
    g = shapely_wkt.loads(r["footprint_wkt"])
    assert g.is_valid
    polys = getattr(g, "geoms", [g])
    assert all(p.exterior.is_ccw for p in polys)
    assert fp.vertex_count(g) <= fp.VERTEX_CAP
    for x, y in fp.coords(g):
        assert round(x, fp.DECIMALS) == x and round(y, fp.DECIMALS) == y


def test_noisy_edge_is_capped():
    # A ragged data edge (a pixel staircase over ~1.4 km) must not reach item bodies.
    t, shape_ = grid(fp.bcgs_cell(TILE))
    rng = np.random.default_rng(0)
    mask = np.ones(shape_, bool)
    edge = (shape_[1] // 2 + rng.integers(-40, 40, shape_[0])).clip(1, shape_[1] - 1)
    for i, e in enumerate(edge):
        mask[i, :e] = False
    g = shapely_wkt.loads(compute(mask, t)["footprint_wkt"])
    assert fp.vertex_count(g) <= fp.VERTEX_CAP


def test_a_non_bcgs_tile_is_clipped_to_its_extent():
    name = "dem_055_154.tif"
    t = Affine(2.0, 0, 550000, 0, -2.0, 1540000)
    mask = np.zeros((500, 500), bool)
    mask[:, :250] = True
    r = fp.footprint_from_mask(mask, t, "EPSG:3005", name)
    g = fp.geom_native(r["footprint_wkt"], "EPSG:3005")
    assert g.within(box(550000, 1539000, 551000, 1540000).buffer(0.5))
    assert g.area == pytest.approx(500 * 1000, rel=0.02)


# =============================================================================
# Item fields
# =============================================================================

def test_bbox_is_the_bounds_of_the_rounded_geometry():
    t, mask, _, _ = gap_mask(0.4)
    r = compute(mask, t)
    geom, bbox = fp.item_geometry(r["footprint_wkt"], TILE)
    assert bbox == list(shape(geom).bounds)


def test_cell_rows_give_the_cell_as_geometry():
    geom, bbox = fp.item_geometry("", TILE)
    assert shape(geom).equals(fp.bcgs_cell(TILE))
    assert bbox == pytest.approx([-123.0, 49.3, -122.975, 49.3125])


def test_a_cell_row_for_a_non_bcgs_tile_is_refused():
    with pytest.raises(ValueError):
        fp.item_geometry("", "dem_055_154.tif")


def test_checksum_is_a_sha256_multihash():
    import hashlib
    h = hashlib.sha256(b"abc")
    assert fp.multihash(h) == "1220" + h.hexdigest()
