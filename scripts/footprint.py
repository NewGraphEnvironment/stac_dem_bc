"""Tile footprints: the part of a DEM tile that holds data (#2).

Until #2 every item's geometry was its raster extent, nodata included, so a
spatial search returned tiles whose nodata an AOI fell in, and neighbouring
tiles overlapped. The footprint is:

  BCGS 1:2,500 tiles   the grid cell named by the tile id, minus any no-data
                       inside it. Delivered rasters are the UTM rectangle around
                       the cell plus ~22 m of overlap with the neighbours
                       (measured, findings Phase 1), so a full tile's footprint
                       is exactly its cell, and neighbours tile without overlap.
  every other tile     the valid data, inside the raster extent. Ids without
                       quadrants are not full 1:20k cells (bc_082e002_... is a
                       1.9 x 1.5 km raster), and albers10k2m tiles have no cell.

A full BCGS tile stores no geometry in the cache: an empty `footprint_wkt`
means "the cell", which is derivable from the id. That keeps data/footprints.csv
small, and keeps cell geometry defined in one place.

The cell is computed from the id, never inferred from the raster. The numbering
was established by measurement, and the obvious assumption was wrong: 1:20k
numbers count from the SW corner of the 1:250k letter block (eastward, rows going
north), and quadrants 1,2 are the SOUTH pair. A NW-origin parse mirrored every
cell about its block's mid-latitude.

Geometry hygiene, because these land in ~102k item bodies and in a committed CSV:
specks and holes under MIN_AREA_M2 are dropped/filled, edges are simplified
OUTWARD (a buffer of the tolerance before a simplify of the same tolerance, so
real data is never cut off - a false negative in every search), the vertex count
is capped by doubling the tolerance, exterior rings are counter-clockwise
(RFC 7946), and coordinates are rounded to DECIMALS before the bbox is taken, so
bbox is exactly the bounds of what is published.
"""

import hashlib
import json
import os
import re
import tempfile
import time
import urllib.request

import numpy as np
import rasterio
import rasterio.enums
import rasterio.features
from rasterio.warp import transform_geom
import shapely
from shapely.geometry import MultiPolygon, Polygon, box, mapping, shape
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from stac_utils import BBOX_BC, encode_url_for_gdal

# BCGS is defined on NAD83 geographic coordinates.
CELL_CRS = "EPSG:4269"
OUT_CRS = "EPSG:4326"
DECIMALS = 7            # ~1 cm; geometry and bbox
PROJ_DECIMALS = 2       # proj:geometry, as published values already are
VERTEX_CAP = 100
MIN_AREA_M2 = 1000.0    # specks dropped, holes filled, below this (scaled up for coarse reads)
FULL_TOLERANCE = 1e-4   # share of the cell allowed to lack data and still be "the cell"
VALID_MIN, VALID_MAX = -100.0, 5000.0   # LidarBC tiles can carry undeclared -3.4e38

# Files at or under this size are downloaded whole: it gives the exact
# footprint AND the checksum, and small strip-organised TIFFs cost one range
# request per row when read remotely. Larger files are read at their coarsest
# overview when they have one.
DOWNLOAD_MAX_BYTES = 64 * 1024 * 1024
HTTP_TIMEOUT = 180
ATTEMPTS = 3

METHOD_DOWNLOAD = "download"
METHOD_OVERVIEW = "overview"

BCGS_RE = re.compile(
    r"^bc(?:ts)?_(\d{2})(\d)([a-p])(\d{3})_?([1-4])_?([1-4])_?([1-4])_")
LETTERS = "abcdefghijklmnop"


class FootprintEmpty(ValueError):
    """A tile with no valid data has no footprint; it must not get an empty one."""


class FootprintNoGeoref(ValueError):
    """A raster with no CRS, or an identity geotransform: its pixels have no place on the ground.

    rasterio warns and carries on with pixel coordinates, so without this a
    non-BCGS tile would get a footprint near 0 deg, 0 deg and nothing would object.
    """


class FootprintOutsideBC(ValueError):
    """A footprint that does not lie inside BBOX_BC: a mislabelled CRS on a tile with no cell to check against."""


class FootprintCrsMismatch(ValueError):
    """A BCGS tile whose cell does not touch its raster in the CRS the file declares.

    Seen on two 2024 tiles named `..._utm11_...` that declare UTM zone 14
    (EPSG:6657): their published items sit in Manitoba. The cell from the id is
    right and the file's CRS is wrong, so a footprint taken from its pixels
    would be wrong too. Refused and reported, never repaired here.
    """


# =============================================================================
# The cell
# =============================================================================

def bcgs_cell(name: str) -> Polygon | None:
    """The BCGS 1:2,500 cell a tile id names, in NAD83 lon/lat; None if it names none.

    `name` is the file's basename. Three spellings name the same cell:
    `bc_092g036_1_1_1_...`, `bc_092g036111_...` (2016 deliveries) and
    `bcts_092g036_1_1_1_...` (BC Timber Sales).
    """
    m = BCGS_RE.match(name)
    if not m:
        return None
    series, band, letter, n20, *quads = m.groups()
    n20 = int(n20)
    if not 1 <= n20 <= 100:
        return None
    # NTS 1:1M block NN-M: east edge at -(48 + 8*NN), south edge at 40 + 4*M.
    east = -(48 + 8 * int(series))
    south = 40 + 4 * int(band)
    # Letters a-p: 1 deg x 2 deg, boustrophedon from the SE corner.
    i = LETTERS.index(letter)
    row, k = divmod(i, 4)
    col_from_east = k if row % 2 == 0 else 3 - k
    # Work in integer units of the 1:2,500 cell (0.0125 deg lat, 0.025 deg lon),
    # so the corners are exact multiples rather than accumulated float error.
    y = (south + row) * 80                       # 80 cells per degree of latitude
    x = (east - 2 * (col_from_east + 1)) * 40    # 40 cells per degree of longitude
    r20, c20 = divmod(n20 - 1, 10)               # 1:20k from the SW corner
    y += r20 * 8
    x += c20 * 8
    size = 8
    for q in quads:
        size //= 2
        q = int(q) - 1                            # 0=SW 1=SE 2=NW 3=NE
        x += size * (q % 2)
        y += size * (q // 2)
    w, s = round(x * 0.025, DECIMALS), round(y * 0.0125, DECIMALS)
    e, n = round((x + 1) * 0.025, DECIMALS), round((y + 1) * 0.0125, DECIMALS)
    return box(w, s, e, n)


def _densify_ll(poly: Polygon, n: int = 16) -> Polygon:
    """Add points along a lon/lat box's edges, so it bends correctly when projected."""
    pts = []
    c = list(poly.exterior.coords)
    for (x0, y0), (x1, y1) in zip(c[:-1], c[1:]):
        for t in np.linspace(0, 1, n, endpoint=False):
            pts.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0)))
    return Polygon(pts)


def cell_in_crs(cell: Polygon, crs) -> Polygon:
    """The cell in a raster's CRS, densified so its edges follow the parallels and meridians."""
    return shape(transform_geom(CELL_CRS, crs, mapping(_densify_ll(cell))))


# =============================================================================
# Masks and polygons
# =============================================================================

def valid_mask(a: np.ndarray, nodata) -> np.ndarray:
    """True where a cell holds an elevation: invalid = nodata OR non-finite OR out of range."""
    m = np.isfinite(a) & (a >= VALID_MIN) & (a <= VALID_MAX)
    if nodata is not None and np.isfinite(nodata):
        m &= a != nodata
    return m


def rasterize(geom, transform, out_shape) -> np.ndarray:
    return rasterio.features.rasterize(
        [(mapping(geom), 1)], out_shape=out_shape, transform=transform,
        fill=0, dtype="uint8").astype(bool)


def polygonize(mask: np.ndarray, transform):
    polys = [shape(g) for g, v in rasterio.features.shapes(
        mask.astype("uint8"), mask=mask, transform=transform) if v == 1]
    return unary_union(polys)


def _polygons(g):
    """The polygonal parts of a geometry, dropping the lines and points an intersection can leave."""
    if g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    return [p for part in getattr(g, "geoms", []) for p in _polygons(part)]


def _hygiene(g, min_area: float):
    """Drop parts smaller than `min_area` and fill holes smaller than it."""
    out = []
    for p in _polygons(g):
        if p.area < min_area:
            continue
        holes = [h for h in p.interiors if Polygon(h).area >= min_area]
        out.append(Polygon(p.exterior, holes))
    return unary_union(out) if out else Polygon()


def vertex_count(g) -> int:
    return int(shapely.get_num_coordinates(g))


def coords(g):
    for p in _polygons(g):
        yield from p.exterior.coords
        for h in p.interiors:
            yield from h.coords


def _round(g, decimals: int):
    return shapely.transform(g, lambda c: np.round(c, decimals))


def _finish_ll(g):
    """Round, orient CCW, and make valid a lon/lat geometry about to be published."""
    g = _round(g, DECIMALS)
    if not g.is_valid:
        g = shapely.make_valid(g)
    parts = [orient(p, sign=1.0) for p in _polygons(g)]
    if not parts:
        raise FootprintEmpty("footprint vanished while rounding")
    return parts[0] if len(parts) == 1 else MultiPolygon(parts)


def _wkt(g) -> str:
    # Not shapely.to_wkt's default: it rounds to 6 decimals, below DECIMALS.
    return shapely.to_wkt(g, rounding_precision=DECIMALS, trim=True)


# =============================================================================
# The footprint
# =============================================================================

def footprint_from_mask(mask: np.ndarray, transform, crs, name: str) -> dict:
    """Footprint of one tile from its validity mask.

    Returns {"footprint_wkt", "valid_percent"}: WKT in EPSG:4326, or "" when the
    tile is a BCGS tile whose data covers its whole cell. Raises FootprintEmpty
    when nothing is valid.
    """
    if crs is None or transform.is_identity:
        raise FootprintNoGeoref(f"{name}: no CRS or no geotransform")
    if not mask.any():
        raise FootprintEmpty(f"{name}: no valid cells")
    valid_percent = round(float(mask.mean()) * 100, 2)
    res = abs(transform.a)
    tol = 2 * res
    min_area = max(MIN_AREA_M2, (4 * res) ** 2)

    data = _hygiene(polygonize(mask, transform), min_area)
    if data.is_empty:
        raise FootprintEmpty(f"{name}: only specks under {min_area:.0f} m2")

    h, w = mask.shape
    extent = box(*_extent(transform, h, w))
    cell = bcgs_cell(name)
    if cell is not None:
        clip = cell_in_crs(cell, crs)
        # Not "cell inside raster": some deliveries crop rasters to their data
        # (2017 082e tiles start 88 m inside the cell, or are a 374 px sliver).
        # A mislabelled UTM zone puts the raster hundreds of km from its cell.
        if not clip.intersects(extent):
            raise FootprintCrsMismatch(
                f"{name}: its cell does not touch the raster in the declared CRS {crs}")
        if clip.difference(data).area <= FULL_TOLERANCE * clip.area:
            return {"footprint_wkt": "", "valid_percent": valid_percent}
    else:
        clip = extent

    t = tol
    while True:
        g = data.buffer(t, join_style="mitre", mitre_limit=2.0).simplify(t)
        g = unary_union(_polygons(g.intersection(clip)))
        if g.is_empty:
            raise FootprintEmpty(f"{name}: no data inside the {'cell' if cell else 'extent'}")
        ll = _finish_ll(shape(transform_geom(crs, OUT_CRS, mapping(g))))
        if vertex_count(ll) <= VERTEX_CAP:
            break
        t *= 2
    if not within_bc(ll):
        raise FootprintOutsideBC(f"{name}: footprint {tuple(round(v, 3) for v in ll.bounds)} is outside BC")
    return {"footprint_wkt": _wkt(ll), "valid_percent": valid_percent}


BC_SLACK_DEG = 0.5   # tiles on the 60th parallel carry data metres past it; a wrong zone is hundreds of km


def within_bc(g) -> bool:
    w, s, e, n = BBOX_BC
    return g.within(box(w, s, e, n).buffer(BC_SLACK_DEG, join_style="mitre"))


def _extent(transform, h, w):
    xs = [transform.c, transform.c + transform.a * w]
    ys = [transform.f, transform.f + transform.e * h]
    return min(xs), min(ys), max(xs), max(ys)


def geom_native(footprint_wkt: str, crs, name: str | None = None):
    """A cache row's footprint in a raster's CRS (the cell, if the row stores none)."""
    if footprint_wkt:
        g = shapely.from_wkt(footprint_wkt)
        return shape(transform_geom(OUT_CRS, crs, mapping(g)))
    return cell_in_crs(bcgs_cell(name), crs)


def item_geometry(footprint_wkt: str, name: str) -> tuple[dict, list]:
    """(geometry, bbox) for an item, from a cache row. bbox is the bounds of exactly what is published."""
    if footprint_wkt:
        g = shapely.from_wkt(footprint_wkt)
    else:
        g = bcgs_cell(name)
        if g is None:
            raise ValueError(f"{name}: an empty footprint means 'the cell', and this id names none")
        g = orient(g, sign=1.0)
    geom = json.loads(json.dumps(mapping(g)))
    return geom, list(shape(geom).bounds)


def proj_geometry(geom: dict, crs, proj_bbox: list | None) -> dict:
    """An item geometry in its native CRS, inside proj:bbox, rounded as published."""
    g = shape(transform_geom(OUT_CRS, crs, geom))
    if proj_bbox:
        g = unary_union(_polygons(g.intersection(box(*proj_bbox))))
    g = _round(g, PROJ_DECIMALS)
    if not g.is_valid:
        g = shapely.make_valid(g)
    parts = [orient(p, sign=1.0) for p in _polygons(g)]
    g = parts[0] if len(parts) == 1 else MultiPolygon(parts)
    return json.loads(json.dumps(mapping(g)))


# =============================================================================
# Reading a tile
# =============================================================================

def multihash(h) -> str:
    """A sha256 as a multihash, hex lowercase, as the STAC file extension wants: `1220` + digest."""
    return "1220" + h.hexdigest()


def _content_length(url: str) -> int | None:
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        n = r.headers.get("Content-Length")
    return int(n) if n else None


def _download(url: str, workdir: str, expected: int | None = None):
    """Stream `url` to a temp file in `workdir`, hashing as it goes. Caller deletes the file.

    urllib returns a short body without raising, so the size is checked against
    Content-Length: a truncated file would otherwise be hashed as if whole.
    """
    h = hashlib.sha256()
    size = 0
    fd, path = tempfile.mkstemp(suffix=".tif", dir=workdir)
    try:
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as r:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                h.update(chunk)
                out.write(chunk)
                size += len(chunk)
        if expected is not None and size != expected:
            raise RuntimeError(f"short read: {size} of {expected} bytes")
    except BaseException:
        os.remove(path)
        raise
    return path, multihash(h), size


# A downloaded file is read whole only up to this many pixels. 954 non-COG
# mapsheet tiles are 11k x 13k at 1 m (~520 MB, strip-organised, no overviews):
# full resolution is ~650 MB of float32 plus a 160 Mpx polygonize per worker,
# which a 16 GB runner cannot hold 16 times over. Decimated on read instead
# (nearest), like an overview; the checksum still covers the whole download.
MAX_READ_PIXELS = 25_000_000


def _read(path, overview_level=None):
    kw = {} if overview_level is None else {"overview_level": overview_level}
    with rasterio.open(path, **kw) as src:
        # Before any decimation: a scaled identity transform is no longer identity.
        if src.crs is None or src.transform.is_identity:
            raise FootprintNoGeoref(f"{os.path.basename(str(path))}: no CRS or no geotransform")
        h, w = src.height, src.width
        f = max(1, int(np.ceil(np.sqrt(h * w / MAX_READ_PIXELS))))
        if f == 1:
            return valid_mask(src.read(1), src.nodata), src.transform, src.crs
        oh, ow = -(-h // f), -(-w // f)
        a = src.read(1, out_shape=(oh, ow), resampling=rasterio.enums.Resampling.nearest)
        transform = src.transform * src.transform.scale(w / ow, h / oh)
        return valid_mask(a, src.nodata), transform, src.crs


def footprint_read(url: str, workdir: str) -> dict:
    """Read one tile and return its cache row (without the url).

    Small files are downloaded whole into `workdir` and deleted before
    returning, so disk use is bounded by workers x file size. Large files with
    overviews are read at their coarsest overview over HTTP and carry no
    checksum, because their bytes were never all seen.
    """
    name = url.rsplit("/", 1)[1]
    http = encode_url_for_gdal(url)
    last = None
    for attempt in range(ATTEMPTS):
        try:
            length = _content_length(http)
            if length is None or length > DOWNLOAD_MAX_BYTES:
                vsi = f"/vsicurl/{http}"
                # A failed /vsicurl/ open is cached in-process; without this a
                # retry sends no request at all.
                # Readdir stays on: albers10k2m overviews are an external .ovr sidecar.
                with rasterio.Env(CPL_VSIL_CURL_NON_CACHED=vsi):
                    with rasterio.open(vsi) as src:
                        ovr = src.overviews(1)
                    if ovr:
                        mask, transform, crs = _read(vsi, overview_level=len(ovr) - 1)
                        row = footprint_from_mask(mask, transform, crs, name)
                        return {"method": METHOD_OVERVIEW, **row, "checksum": "", "size": length or ""}
            path, checksum, size = _download(http, workdir, length)
            try:
                mask, transform, crs = _read(path)
            finally:
                os.remove(path)
            row = footprint_from_mask(mask, transform, crs, name)
            return {"method": METHOD_DOWNLOAD, **row, "checksum": checksum, "size": size}
        except (FootprintEmpty, FootprintCrsMismatch, FootprintNoGeoref, FootprintOutsideBC):
            raise                                  # deterministic: retrying cannot help
        except Exception as e:                     # noqa: BLE001 - network, by assumption
            last = e
            if attempt < ATTEMPTS - 1:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{name}: {last}")


# =============================================================================
# Onto an item
# =============================================================================
# One dict-based function serves both writers: item_create builds a pystac Item
# and passes its dict through here, and the one-time rewrite edits published
# bodies with it. Two copies would drift, and the drift would be invisible
# until someone compared an item built monthly with one rewritten (AC2).

FILE_EXT = "https://stac-extensions.github.io/file/v2.1.0/schema.json"
RASTER_EXT = "https://stac-extensions.github.io/raster/v1.1.0/schema.json"
FOOTPRINT_METHODS = {METHOD_DOWNLOAD, METHOD_OVERVIEW}


def _item_crs(props: dict):
    if props.get("proj:epsg"):
        return f"EPSG:{int(props['proj:epsg'])}"
    if props.get("proj:wkt2"):            # 160 items carry no epsg (research/pgstac_round_trip.md)
        return rasterio.crs.CRS.from_wkt(props["proj:wkt2"])
    return None


def item_footprint_apply(item: dict, row: dict | None, dem_key: str) -> list[str]:
    """Write a cache row's footprint onto an item dict. Returns what changed.

    A row that is missing or refused (empty, crs_mismatch) changes nothing: the
    item keeps the geometry it was built or published with, rather than one
    regenerated here - for ~58k rio_stac-built items that would silently swap a
    reprojected quad for a box.
    """
    if not row or row.get("method") not in FOOTPRINT_METHODS:
        return []
    before = json.dumps(item, sort_keys=True)
    name = item["assets"][dem_key]["href"].rsplit("/", 1)[1]
    geom, bbox = item_geometry(row["footprint_wkt"], name)
    item["geometry"], item["bbox"] = geom, bbox

    props = item["properties"]
    changed_ext = set(item.get("stac_extensions", []))
    if "proj:geometry" in props:
        crs = _item_crs(props)
        if crs is not None:
            props["proj:geometry"] = proj_geometry(geom, crs, props.get("proj:bbox"))

    dem = item["assets"][dem_key]
    bands = dem.get("raster:bands") or [{}]
    bands[0] = {**bands[0], "statistics": {**bands[0].get("statistics", {}),
                                           "valid_percent": float(row["valid_percent"])}}
    dem["raster:bands"] = bands
    changed_ext.add(RASTER_EXT)
    if row.get("checksum"):
        dem["file:checksum"] = row["checksum"]
        dem["file:size"] = int(row["size"])
        changed_ext.add(FILE_EXT)
    item["stac_extensions"] = sorted(changed_ext)

    if json.dumps(item, sort_keys=True) == before:
        return []
    return ["footprint"]
