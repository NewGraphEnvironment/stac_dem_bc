"""footprint_apply.py end to end, offline: a local collection, a stubbed fetch.

What matters: a full run refuses an incomplete cache; an item keeps its
geometry unless a footprint row replaces it; a re-run rewrites nothing; and
the manifest is this migration's own.
"""

import csv
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import footprint_apply as fa  # noqa: E402
import item_rewrite  # noqa: E402
from footprint_extract import FIELDS  # noqa: E402

G = "https://nrs.objectstore.gov.bc.ca/gdwuts/092/092g/2024/dem"
FULL = f"{G}/bc_092g036_1_1_1_xli1m_utm10_20240101_20240101.tif"     # footprint = the cell
REFUSED = f"{G}/bc_092g036_1_1_2_xli1m_utm10_20240101_20240101.tif"  # crs_mismatch
GONE = f"{G}/bc_092g036_1_1_3_xli1m_utm10_20240101_20240101.tif"     # no longer listed
OLD_GEOM = {"type": "Polygon", "coordinates": [[[-123.1, 49.2], [-122.9, 49.2], [-122.9, 49.4],
                                                [-123.1, 49.4], [-123.1, 49.2]]]}


def iid(url):
    return url.split("/gdwuts/")[1].replace("/", "-").removesuffix(".tif")


def body(url):
    return {"type": "Feature", "stac_version": "1.1.0", "id": iid(url), "stac_extensions": [],
            "geometry": OLD_GEOM, "bbox": [-123.1, 49.2, -122.9, 49.4], "collection": "stac-elevation-bc",
            "properties": {"datetime": "2024-01-01T00:00:00Z"}, "links": [],
            "assets": {"dem": {"href": url, "roles": ["data"]}}}


@pytest.fixture
def world(tmp_path, monkeypatch):
    coll = tmp_path / "collection.json"
    coll.write_text(json.dumps({"links": [{"rel": "item", "href": f"https://b/{iid(u)}.json"}
                                          for u in (FULL, REFUSED, GONE)]}))
    listed = tmp_path / "urls_list.txt"
    listed.write_text(f"{FULL}\n{REFUSED}\n")
    fps = tmp_path / "footprints.csv"
    with open(fps, "w", newline="") as fh:
        w = csv.DictWriter(fh, FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerow({"url": FULL, "method": "download", "footprint_wkt": "", "valid_percent": "99.4",
                    "checksum": "1220" + "ab" * 32, "size": "5693457"})
        w.writerow({"url": REFUSED, "method": "crs_mismatch", "footprint_wkt": "", "valid_percent": "",
                    "checksum": "", "size": ""})
    monkeypatch.setattr(fa, "URLS_LIST", str(listed))
    bodies = {iid(u): body(u) for u in (FULL, REFUSED, GONE)}
    fetch = lambda i: json.loads(json.dumps(bodies[i]))      # noqa: E731
    monkeypatch.setattr(item_rewrite, "item_fetch", fetch)
    monkeypatch.setattr(fa, "item_fetch", fetch)
    out = tmp_path / "out"
    argv = ["footprint_apply.py", "--collection", str(coll), "--footprints", str(fps),
            "--out-dir", str(out), "--manifest", str(tmp_path / "done.txt"),
            "--errors-log", str(tmp_path / "err.txt"), "--workers", "2"]
    return tmp_path, out, argv, listed


def run(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", argv)
    return fa.main()


def test_full_run_rewrites_and_keeps_geometry_without_a_row(world, monkeypatch):
    tmp, out, argv, _ = world
    assert run(monkeypatch, argv + ["--verify", "3"]) == 0
    full = json.loads((out / f"{iid(FULL)}.json").read_text())
    assert full["bbox"] == pytest.approx([-123.0, 49.3, -122.975, 49.3125])
    assert full["properties"]["lidarbc:delivery"] == "092/092g/2024"
    assert full["assets"]["dem"]["file:size"] == 5693457
    for u in (REFUSED, GONE):
        b = json.loads((out / f"{iid(u)}.json").read_text())   # lidarbc edit only
        assert b["geometry"] == OLD_GEOM and "raster:bands" not in b["assets"]["dem"]
        assert b["properties"]["lidarbc:delivery"] == "092/092g/2024"
    lines = (tmp / "done.txt").read_text().splitlines()
    assert lines[0] == f"# migration: {fa.MIGRATION}" and len(lines) == 4


def test_a_rerun_rewrites_nothing(world, monkeypatch):
    tmp, out, argv, _ = world
    assert run(monkeypatch, argv) == 0
    for f in out.iterdir():
        f.unlink()
    (tmp / "done.txt").unlink()
    # fetch now returns the rewritten bodies, as S3 would after the sync
    rewritten = {}
    assert run(monkeypatch, argv) == 0
    for f in out.iterdir():
        rewritten[f.stem] = json.loads(f.read_text())
    for f in out.iterdir():
        f.unlink()
    (tmp / "done.txt").unlink()
    monkeypatch.setattr(item_rewrite, "item_fetch", lambda i: json.loads(json.dumps(rewritten[i])))
    assert run(monkeypatch, argv) == 0
    assert list(out.iterdir()) == []                          # everything "unchanged"


def test_a_full_run_refuses_an_incomplete_cache(world, monkeypatch):
    tmp, out, argv, listed = world
    listed.write_text(listed.read_text() + f"{G}/bc_092g036_1_1_4_xli1m_utm10_20240101.tif\n")
    assert run(monkeypatch, argv) == 1
    assert not out.exists() or list(out.iterdir()) == []
    # a rehearsal is allowed to run against a partial cache
    assert run(monkeypatch, argv + ["--limit", "1"]) == 0


def test_another_migrations_manifest_is_refused(world, monkeypatch):
    tmp, out, argv, _ = world
    (tmp / "done.txt").write_text("# migration: 34-collection-rename\nx\n")
    with pytest.raises(RuntimeError):
        run(monkeypatch, argv)


def test_a_full_run_refuses_a_cache_with_faults(world, monkeypatch):
    tmp, out, argv, _ = world
    fps = tmp / "footprints.csv"
    rows = fps.read_text().replace(",download,,", ",download,\"POLYGON ((-101.5 49, -101.4 49, -101.4 49.1, -101.5 49))\",", 1)
    fps.write_text(rows)
    assert run(monkeypatch, argv) == 1
