"""Change detection refuses a listing or cache whose URLs are not `https://` (#51).

detect_changes.R compares a fresh bucket listing against data/urls_list.txt as
strings. ngr before 0.0.3 wrote `https:/host/...` (ngr#38), so a cache in one
spelling against a listing in the other reads as every URL new AND every URL
deleted -- at an unchanged count, which the 90% plausibility guard cannot see.

These run the real detect_changes.R in a scratch directory, with the bucket walk
replaced by a fixed listing, so what is asserted is the workflow's contract: the
exit code (2 = error, never 1 = "changes detected") and an untouched cache.
"""

import os
import shutil
import subprocess

import pytest

REPO = os.path.join(os.path.dirname(__file__), "..")
RSCRIPT = shutil.which("Rscript")

# Skipped on a machine without R; never in CI, where update.yml installs R
# before pytest and a silent skip would ship the guard untested.
pytestmark = pytest.mark.skipif(RSCRIPT is None and not os.environ.get("CI"),
                                reason="Rscript not installed")

BUCKET = "https://nrs.objectstore.gov.bc.ca/gdwuts"
DEM = [f"{BUCKET}/093/093l/2019/dem/bc_093l0{i}_xl1m.tif" for i in range(3)]
DSM = [f"{BUCKET}/093/093l/2019/dsm/bc_093l0{i}_xl1m.tif" for i in range(3)]


def _single(urls):
    return [u.replace("https://", "https:/", 1) for u in urls]


def _workspace(tmp_path, listing_dem, cache_dem, listing_dsm=DSM, cache_dsm=DSM):
    """A scratch repo whose bucket walk returns a fixed listing.

    Only the network call is replaced: the shipped urls_listing_fetch() runs
    with `keys_get` swapped for a function returning the fixture keys, so the
    walk guard under test is the one in the shipped function body, not a copy.
    """
    (tmp_path / "scripts").mkdir()
    (tmp_path / "data").mkdir()
    shutil.copy(os.path.join(REPO, "scripts", "detect_changes.R"), tmp_path / "scripts")
    real = os.path.join(REPO, "scripts", "urls_listing.R")
    with open(real) as f:
        listing_src = f.read()

    keys = ", ".join(f'"{u}"' for u in listing_dem + listing_dsm)
    listing_src += f"""
urls_listing_fetch_shipped <- urls_listing_fetch
urls_listing_fetch <- function(url_bucket = URL_BUCKET, keys_min = KEYS_MIN) {{
  cat("WALK REACHED\\n")
  urls_listing_fetch_shipped(url_bucket, keys_min = 0,
                             keys_get = function(...) c({keys}))
}}
"""
    (tmp_path / "scripts" / "urls_listing.R").write_text(listing_src)
    (tmp_path / "data" / "urls_list.txt").write_text("\n".join(cache_dem) + "\n")
    (tmp_path / "data" / "urls_dsm.txt").write_text("\n".join(cache_dsm) + "\n")
    return tmp_path


def _run(ws):
    return subprocess.run([RSCRIPT, "scripts/detect_changes.R"], cwd=ws,
                          capture_output=True, text=True, timeout=120)


def test_matching_https_listing_and_cache_report_no_changes(tmp_path):
    ws = _workspace(tmp_path, DEM, DEM)
    r = _run(ws)
    assert r.returncode == 0, r.stdout + r.stderr


def test_single_slash_cache_is_refused_not_reported_as_changes(tmp_path):
    """The failure the ngr bump would cause: same files, two spellings."""
    ws = _workspace(tmp_path, DEM, _single(DEM))
    before = (ws / "data" / "urls_list.txt").read_text()
    r = _run(ws)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "do not start with https://" in r.stdout
    # Refused before the ~575k-key bucket walk, not after it.
    assert "WALK REACHED" not in r.stdout
    assert (ws / "data" / "urls_list.txt").read_text() == before
    assert not (ws / "data" / "urls_new.txt").exists()
    assert not (ws / "data" / "urls_deleted.txt").exists()


def test_single_slash_listing_is_refused(tmp_path):
    """An old ngr against a migrated cache: the walk itself is refused."""
    ws = _workspace(tmp_path, _single(DEM), DEM, listing_dsm=_single(DSM))
    before = (ws / "data" / "urls_list.txt").read_text()
    r = _run(ws)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "bucket walk" in r.stdout
    assert (ws / "data" / "urls_list.txt").read_text() == before


def test_single_slash_dsm_cache_is_refused(tmp_path):
    # The listing has one DEM the cache lacks, so an unrefused run would write
    # urls_new.txt and rewrite urls_list.txt before reaching the DSM side.
    ws = _workspace(tmp_path, DEM, DEM[:-1], cache_dsm=_single(DSM))
    before_dem = (ws / "data" / "urls_list.txt").read_text()
    before = (ws / "data" / "urls_dsm.txt").read_text()
    r = _run(ws)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "urls_dsm.txt" in r.stdout
    assert (ws / "data" / "urls_dsm.txt").read_text() == before
    # Refused before the DEM side wrote anything either.
    assert not (ws / "data" / "urls_new.txt").exists()
    assert (ws / "data" / "urls_list.txt").read_text() == before_dem


def test_single_slash_deleted_audit_trail_is_refused(tmp_path):
    """urls_deleted.txt is merged with unique(); two spellings would both survive."""
    ws = _workspace(tmp_path, DEM, DEM)
    (ws / "data" / "urls_deleted.txt").write_text(_single(DEM)[0] + "\n")
    r = _run(ws)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "urls_deleted.txt" in r.stdout


def test_no_tracked_data_file_holds_a_one_slash_url():
    """The migration's invariant, held for good: one spelling across data/.

    Searches every tracked file under data/ as bytes, anywhere in a line, so a
    URL inside a CSV cell or JSON string counts. Binary files (NUL in the first
    8 KiB) are skipped, and the test fails if that skips everything.
    """
    import re

    files = subprocess.run(["git", "ls-files", "data/"], cwd=REPO, capture_output=True,
                           text=True, check=True).stdout.split()
    one_slash = re.compile(rb"https:/(?!/)")
    scanned, offenders = 0, []
    for rel in files:
        with open(os.path.join(REPO, rel), "rb") as f:
            raw = f.read()
        if b"\0" in raw[:8192]:
            continue
        scanned += 1
        n = len(one_slash.findall(raw))
        if n:
            offenders.append(f"{rel}: {n}")
    assert scanned >= 4, f"only {scanned} text files scanned under data/"
    assert not offenders, "one-slash URLs (#51): " + ", ".join(offenders)
