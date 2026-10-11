"""Contract tests for the footprint cache builder (#2), offline.

The read is injected, so what is tested is the shipped run loop: what reaches
the cache, what is retried, what is refused, and that nothing is left on disk.
"""

import csv
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import footprint_extract as fe  # noqa: E402
from footprint import FootprintCrsMismatch, FootprintEmpty  # noqa: E402

B = "https://nrs.objectstore.gov.bc.ca/gdwuts/092/092g/2024/dem"
U = [f"{B}/bc_092g036_1_1_{q}_xli1m_utm10_20240101.tif" for q in "1234"]


def ok(url, workdir):
    # Leave a file behind, as a download would mid-flight, to prove cleanup.
    open(os.path.join(workdir, os.path.basename(url)), "w").write("x")
    return {"method": "download", "footprint_wkt": "", "valid_percent": 99.1,
            "checksum": "1220ab", "size": 10}


def flaky(url, workdir):
    if url.endswith("_3_xli1m_utm10_20240101.tif"):
        raise RuntimeError("connection reset")
    if url.endswith("_4_xli1m_utm10_20240101.tif"):
        raise FootprintEmpty("no valid cells")
    if url.endswith("_2_xli1m_utm10_20240101.tif"):
        raise FootprintCrsMismatch("zone 14")
    return ok(url, workdir)


def rows(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def go(tmp_path, todo, read, **kw):
    args = dict(workers=2, max_minutes=None, min_free_gb=0, population=len(todo))
    args.update(kw)
    return fe.run(todo, str(tmp_path / "c.csv"), str(tmp_path / "e.txt"), read=read,
                  tmp_root=str(tmp_path), **args)


def test_outcomes_land_where_they_belong(tmp_path):
    code, stats = go(tmp_path, U, flaky, population=0)
    got = {r["url"]: r["method"] for r in rows(tmp_path / "c.csv")}
    assert got == {U[0]: "download", U[1]: "crs_mismatch", U[3]: "empty"}
    # the transient failure is logged and NOT cached, so the next run retries it
    assert open(tmp_path / "e.txt").read().startswith(U[2])
    assert stats["error"] == 1
    assert code == fe.EXIT_ERRORS           # 1 of 4 is over tolerance on a rehearsal


def test_a_resumed_run_reads_only_what_is_missing(tmp_path):
    go(tmp_path, U, flaky, population=0)
    todo = fe.todo_select(U, fe.cache_load(str(tmp_path / "c.csv")))
    assert todo == [U[2]]


def test_nothing_is_left_on_disk(tmp_path):
    go(tmp_path, U, ok)
    assert [p for p in os.listdir(tmp_path) if p.startswith("footprint_")] == []


def test_low_disk_stops_cleanly(tmp_path):
    code, stats = go(tmp_path, U, ok, min_free_gb=1e9)
    assert code == fe.EXIT_DISK and stats["written"] == 0


def test_out_of_time_stops_without_failing(tmp_path):
    code, stats = go(tmp_path, U, ok, max_minutes=0)
    assert code == fe.EXIT_OK and stats["stopped"] == "max-minutes" and stats["written"] == 0


def test_a_partial_last_line_is_dropped_and_finalize_sorts(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text("url,method,footprint_wkt,valid_percent,checksum,size\n"
                 f"{U[1]},download,,99.0,1220aa,5\n{U[0]},download,,98.0,1220bb,5\n{U[2]},downl")
    assert set(fe.cache_load(str(p))) == {U[0], U[1]}
    assert fe.cache_finalize(str(p)) == 2
    assert [r["url"] for r in rows(p)] == [U[0], U[1]]
    assert p.read_text().endswith("\n")


@pytest.mark.parametrize("bad", [
    f"{U[0]},guessed,,99.0,,\n",                                # unknown method
    f"{U[0]},download\n",                                        # short row
    f"{U[0].replace('https://', 'https:/')},download,,99,,\n",   # one-slash URL (#51)
])
def test_a_malformed_cache_is_refused(tmp_path, bad):
    p = tmp_path / "c.csv"
    p.write_text("url,method,footprint_wkt,valid_percent,checksum,size\n" + bad)
    with pytest.raises(ValueError):
        fe.cache_load(str(p))


def test_changed_is_written_minus_new():
    assert fe.changed_select([U[2], U[0], U[1]], {U[1]}) == [U[0], U[2]]


def test_todo_dedupes_and_skips_cached():
    assert fe.todo_select([U[0], U[1], U[0], U[2]], {U[1]: {}}) == [U[0], U[2]]


def test_new_refusals_are_cached_rows():
    from footprint import FootprintNoGeoref, FootprintOutsideBC

    def r(exc):
        def read(url, workdir):
            raise exc("x")
        return read
    assert fe.row_for(U[0], r(FootprintNoGeoref))["method"] == "no_georef"
    assert fe.row_for(U[0], r(FootprintOutsideBC))["method"] == "outside_bc"


def test_audit_names_a_footprint_outside_bc():
    rows = {U[0]: {"method": "download", "footprint_wkt": "POLYGON ((-101.5 49, -101.4 49, -101.4 49.1, -101.5 49))"},
            U[1]: {"method": "download", "footprint_wkt": "POLYGON ((-123 49.3, -122.9 49.3, -122.9 49.4, -123 49.3))"},
            U[2]: {"method": "download", "footprint_wkt": "not wkt"},
            U[3]: {"method": "download", "footprint_wkt": ""}}
    assert fe.cache_audit(rows) == {"outside BC": [U[0]], "unparseable WKT": [U[2]]}


def test_the_rebuild_list_is_cumulative_and_skips_new_urls(tmp_path):
    changed = tmp_path / "changed.txt"
    changed.write_text(f"{U[3]}\n")                       # left by a run whose sync failed
    go(tmp_path, U[:3], ok, changed_path=str(changed), new_urls={U[1]})
    assert fe.changed_finalize(str(changed)) == 3
    assert changed.read_text().splitlines() == [U[0], U[2], U[3]]


def test_a_url_is_listed_only_once_its_row_is_written(tmp_path):
    changed = tmp_path / "changed.txt"
    go(tmp_path, U, flaky, population=0, changed_path=str(changed), new_urls=set())
    listed = set(changed.read_text().split())
    cached = {r["url"] for r in rows(tmp_path / "c.csv")}
    assert listed == cached and U[2] not in listed       # the transient failure is in neither


def test_prune_drops_only_urls_whose_staged_body_carries_its_fields(tmp_path):
    import json
    from stac_utils import url_to_item_id
    changed = tmp_path / "changed.txt"
    changed.write_text("".join(f"{u}\n" for u in U))
    staged = tmp_path / "staged"
    staged.mkdir()
    row = {"method": "download", "footprint_wkt": "", "valid_percent": "99.0", "checksum": "", "size": ""}
    foot = {u: row for u in U}

    def body(u):
        return {"type": "Feature", "id": url_to_item_id(u), "stac_extensions": [], "links": [],
                "geometry": None, "bbox": None, "properties": {},
                "assets": {"dem": {"href": u, "roles": ["data"]}}}
    from item_fields import item_fields_apply
    done = body(U[0])
    item_fields_apply(done, U[0], foot)                  # rebuilt with its footprint
    (staged / f"{url_to_item_id(U[0])}.json").write_text(json.dumps(done))
    # a backfill dispatch staged U[2]'s published body unchanged: extent, no fields
    (staged / f"{url_to_item_id(U[2])}.json").write_text(json.dumps(body(U[2])))
    # U[1] and U[3]: item_create dropped them, nothing staged
    assert fe.changed_prune(str(changed), str(staged), foot) == (1, 3)
    assert changed.read_text().splitlines() == [U[1], U[2], U[3]]


def test_a_short_download_is_refused(tmp_path):
    import footprint as fp
    src = tmp_path / "src.bin"
    src.write_bytes(b"x" * 1000)
    work = tmp_path / "w"
    work.mkdir()
    with pytest.raises(RuntimeError, match="short read"):
        fp._download(src.as_uri(), str(work), expected=5000)
    assert list(work.iterdir()) == []                     # the partial file is removed
    path, checksum, size = fp._download(src.as_uri(), str(work), expected=1000)
    assert size == 1000 and checksum.startswith("1220")
