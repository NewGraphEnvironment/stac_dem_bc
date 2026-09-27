"""End-to-end tests for scripts/catalogue_register.sh, with no network.

#42: the script takes STAC_COLLECTION / STAC_BUCKET_URL and is documented as
usable for any collection, but its pre-load audit required this repo's `dem`
asset on every item -- so another collection (stac-airphoto-bc, 10,100 items)
was refused in full. The asset half of the audit now follows the collection;
the collection-id half and the count always run.

The same tests pin the ORDER: the audit must refuse before the collection row
is upserted, not after. Before #42 it ran after `collection_register.sh`, so a
refused run had already written the collection.

How it runs offline:
  - collection.json and every item link are `file://` URLs, which `curl -fsSL`
    fetches like any other
  - `ssh` is a stub prepended to PATH. It answers the reachability probe
    (`ssh ... HOST true`) and records every other call -- each one is an
    attempted write -- then fails it, so nothing past the first write runs
  - STAC_HOST names an unresolvable host, so a stub that somehow failed to
    shadow the real ssh still reaches nothing
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from collection_patch import COLLECTION_ID  # noqa: E402
from item_migrate import ASSET_RENAMES  # noqa: E402
from register_manifest import s3_bucket_name  # noqa: E402
from stac_utils import ASSET_DEM, ASSET_DSM, PATH_S3_STAC  # noqa: E402

FOREIGN_ID = "stac-airphoto-bc"
# The airphoto item shape, measured on stac_airphoto_bc's data/stac/.
FOREIGN_ASSETS = ("thumbnail", "flight_log")
OLD_KEY = next(iter(ASSET_RENAMES))

SSH_STUB = """#!/bin/bash
# The probe is `ssh -o ... HOST true`; anything else is a write.
last="${@: -1}"
if [ "$last" = "true" ]; then exit 0; fi
cat > /dev/null
echo write >> "$SSH_LOG"
exit 1
"""

# Network-proof by construction: file:// goes to the real curl, anything else
# fails. A harness bug that pointed the script at the real bucket once started
# fetching 102k items; with this it fails at the first request instead.
CURL_STUB = """#!/bin/bash
for a in "$@"; do
  case "$a" in
    http://*|https://*) echo "curl stub: refusing network URL $a" >&2; exit 7 ;;
  esac
done
exec {real} "$@"
"""


def _asset(key):
    return {key: {"href": f"https://example.invalid/{key}", "type": "image/tiff"}}


def _item(item_id, collection, keys):
    assets = {}
    for k in keys:
        assets.update(_asset(k))
    return {"type": "Feature", "stac_version": "1.0.0", "id": item_id,
            "collection": collection, "geometry": None, "properties": {},
            "links": [], "assets": assets}


def _run(tmp_path, collection_id, items, env_extra=None, args=("--all",),
         stac_collection=None, href_base=None):
    """Publish `items` under `collection_id` as file:// and run the script.

    Returns (CompletedProcess, number of attempted writes).
    """
    bucket = tmp_path / "bucket"
    bucket.mkdir()
    links = []
    for doc in items:
        p = bucket / f"{doc['id']}.json"
        p.write_text(json.dumps(doc))
        href = f"{href_base}/{doc['id']}.json" if href_base else p.as_uri()
        links.append({"rel": "item", "href": href,
                      "type": "application/json"})
    (bucket / "collection.json").write_text(json.dumps(
        {"type": "Collection", "id": collection_id, "stac_version": "1.0.0",
         "description": "fixture", "license": "proprietary",
         "extent": {}, "links": links}))

    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("ssh", SSH_STUB),
                       ("curl", CURL_STUB.format(real=shutil.which("curl")))):
        stub = bindir / name
        stub.write_text(body)
        stub.chmod(0o755)
    log = tmp_path / "ssh.log"

    env = dict(os.environ)
    # Scrub what the caller's shell may carry BEFORE setting the fixture's own
    # values -- the other order deletes the fixture bucket and the script falls
    # back to the real one.
    for k in ("STAC_COLLECTION", "STAC_REQUIRE_ASSET", "STAC_FORBID_ASSET",
              "STAC_BUCKET_URL", "STAC_API", "STAC_HOST", "STAC_DB"):
        env.pop(k, None)
    env.update({
        "PATH": f"{bindir}{os.pathsep}{env['PATH']}",
        "SSH_LOG": str(log),
        "STAC_HOST": "nobody@stub.invalid",
        "STAC_BUCKET_URL": bucket.as_uri(),
        # Unreachable, so the post-registration verify can never touch the
        # real API. Nothing here should get that far anyway.
        "STAC_API": "http://127.0.0.1:9",
        "FETCH_JOBS": "4",
        # Pinned rather than inherited: the script falls back to a cwd-relative
        # .venv and then to python3, which may lack rasterio -- and the startup
        # lookups would then fail every case for a reason unrelated to #42.
        "PYTHON": sys.executable,
    })
    if stac_collection is not None:
        env["STAC_COLLECTION"] = stac_collection
    env.update(env_extra or {})

    proc = subprocess.run(
        # /bin/bash, the shebang's interpreter -- not whatever `bash` is first on
        # PATH. On macOS that is 3.2, where an empty array under `set -u` and
        # other constructs this script leans on behave differently from 5.x.
        ["/bin/bash", "scripts/catalogue_register.sh", *args],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    writes = len(log.read_text().splitlines()) if log.exists() else 0
    return proc, writes


def _out(proc):
    return proc.stdout + proc.stderr


# =============================================================================
# A foreign collection registers
# =============================================================================

def test_foreign_collection_without_dem_passes_the_audit(tmp_path):
    """The #42 case: airphoto-shaped items carry no `dem`, and must not need one."""
    items = [_item(f"a{i}", FOREIGN_ID, FOREIGN_ASSETS) for i in range(3)]
    proc, writes = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID)
    out = _out(proc)
    assert "OK: every item agrees with its collection" in out, out
    assert f"lack asset {ASSET_DEM!r}" not in out
    # It went on to write: the collection upsert was attempted (and the stub
    # failed it, which is why the run exits non-zero here).
    assert writes == 1, out


def test_foreign_collection_says_asset_checks_are_off(tmp_path):
    """Off must be said out loud, or a foreign run reads as a checked pass --
    at startup, and again on the OK line, which arrives much later."""
    items = [_item("a0", FOREIGN_ID, FOREIGN_ASSETS)]
    proc, _ = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID)
    assert "asset audit: none" in proc.stdout, _out(proc)
    assert "OK: every item agrees with its collection (no asset checks)" in proc.stderr


def test_the_policy_is_printed_on_a_dryrun(tmp_path):
    """--dryrun exits before the audit, so the startup line is all it shows."""
    items = [_item("a0", FOREIGN_ID, FOREIGN_ASSETS)]
    proc, writes = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID,
                        args=("--all", "--dryrun"))
    assert proc.returncode == 0, _out(proc)
    assert "asset audit: none" in proc.stdout
    assert writes == 0


def test_foreign_collection_still_audits_homogeneity(tmp_path):
    """Scoping the asset checks must not scope away the collection-id check."""
    items = [_item("a0", FOREIGN_ID, FOREIGN_ASSETS),
             _item("a1", "some-other-collection", FOREIGN_ASSETS)]
    proc, writes = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID)
    out = _out(proc)
    assert proc.returncode != 0
    assert "name another collection" in out, out
    assert writes == 0, "refused after a write -- the audit must run first"


def test_foreign_collection_can_opt_in_to_an_asset_requirement(tmp_path):
    items = [_item("a0", FOREIGN_ID, FOREIGN_ASSETS),
             _item("a1", FOREIGN_ID, ("flight_log",))]
    proc, writes = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID,
                        env_extra={"STAC_REQUIRE_ASSET": "thumbnail"})
    out = _out(proc)
    assert proc.returncode != 0
    assert "lack asset 'thumbnail'" in out, out
    assert writes == 0, "refused after a write -- the audit must run first"


def test_foreign_collection_can_opt_in_to_a_forbidden_asset(tmp_path):
    items = [_item("a0", FOREIGN_ID, FOREIGN_ASSETS),
             _item("a1", FOREIGN_ID, ("thumbnail", "legacy"))]
    proc, writes = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID,
                        env_extra={"STAC_FORBID_ASSET": "legacy"})
    out = _out(proc)
    assert proc.returncode != 0
    assert "retired asset key" in out, out
    assert writes == 0


# =============================================================================
# This repo's collection keeps the #34 guard
# =============================================================================

def test_own_collection_passes_with_the_current_keys(tmp_path):
    """Control for the two refusals below: a good elevation item gets through."""
    items = [_item("d0", COLLECTION_ID, (ASSET_DEM, ASSET_DSM)),
             _item("d1", COLLECTION_ID, (ASSET_DEM,))]
    proc, writes = _run(tmp_path, COLLECTION_ID, items)
    out = _out(proc)
    forbid = ",".join(ASSET_RENAMES)
    assert f"asset audit: require={ASSET_DEM} forbid={forbid}" in proc.stdout, out
    assert (f"OK: every item agrees with its collection "
            f"(require={ASSET_DEM} forbid={forbid})") in proc.stderr, out
    assert writes == 1, out


def test_own_collection_still_requires_dem(tmp_path):
    items = [_item("d0", COLLECTION_ID, (ASSET_DEM,)),
             _item("d1", COLLECTION_ID, (ASSET_DSM,))]
    proc, writes = _run(tmp_path, COLLECTION_ID, items)
    out = _out(proc)
    assert proc.returncode != 0
    assert f"lack asset {ASSET_DEM!r}" in out, out
    assert writes == 0, "refused after a write -- the audit must run first"


def test_own_collection_still_forbids_the_retired_key(tmp_path):
    """Half a rename: right collection, retired key beside the new one."""
    items = [_item("d0", COLLECTION_ID, (ASSET_DEM,)),
             _item("d1", COLLECTION_ID, (ASSET_DEM, OLD_KEY))]
    proc, writes = _run(tmp_path, COLLECTION_ID, items)
    out = _out(proc)
    assert proc.returncode != 0
    assert "retired asset key" in out, out
    assert writes == 0


def test_own_collection_named_explicitly_is_still_own(tmp_path):
    """STAC_COLLECTION set to this repo's id must not read as foreign."""
    items = [_item("d0", COLLECTION_ID, (ASSET_DSM,))]
    proc, writes = _run(tmp_path, COLLECTION_ID, items,
                        stac_collection=COLLECTION_ID)
    assert proc.returncode != 0
    assert f"lack asset {ASSET_DEM!r}" in _out(proc), _out(proc)
    assert writes == 0


@pytest.mark.parametrize("var", ["STAC_REQUIRE_ASSET", "STAC_FORBID_ASSET"])
def test_own_collection_refuses_an_asset_override(tmp_path, var):
    """The elevation rules come from the modules; env must not loosen them."""
    items = [_item("d0", COLLECTION_ID, (ASSET_DEM,))]
    proc, writes = _run(tmp_path, COLLECTION_ID, items,
                        env_extra={var: "anything"})
    out = _out(proc)
    assert proc.returncode != 0
    assert var in out, out
    # Refused at startup, before the fetch or anything else.
    assert "fetching" not in proc.stdout
    assert writes == 0


# Every spelling of this repo's bucket. All four non-trivial ones were checked
# live to answer 200 for collection.json (code-check round 1, 2026-09-27); a
# plain string compare read each as someone else's catalogue.
OWN_BUCKET_ALIASES = [
    PATH_S3_STAC,
    PATH_S3_STAC + "/",
    "https://stac-dem-bc.s3.us-west-2.amazonaws.com",
    "http://stac-dem-bc.s3.amazonaws.com",
    "https://s3.us-west-2.amazonaws.com/stac-dem-bc",
    "https://STAC-DEM-BC.s3.amazonaws.com",
]


@pytest.mark.parametrize("bucket", OWN_BUCKET_ALIASES)
def test_this_repos_bucket_is_this_repos_catalogue_whatever_the_id(tmp_path, bucket):
    """The rename window. Between merging a rename and the cutover, this bucket
    still publishes the OLD id, and STAC_COLLECTION set to it passes the id
    reconciliation. Keyed on the id alone, the run would read as foreign and
    load old-shape items unchecked. Keyed on the bucket too, the override is
    refused -- at startup, so nothing is fetched from the real bucket."""
    proc, writes = _run(tmp_path, FOREIGN_ID, [], stac_collection="some-old-id",
                        env_extra={"STAC_BUCKET_URL": bucket,
                                   "STAC_REQUIRE_ASSET": "thumbnail"})
    out = _out(proc)
    assert proc.returncode != 0
    assert "STAC_REQUIRE_ASSET" in out and "this repo's catalogue" in out, out
    assert "fetching" not in proc.stdout
    assert writes == 0


def test_audit_items_reports_a_forbid_list_that_parses_to_nothing(tmp_path):
    """`--forbid-asset ,` is a non-empty argument naming no key. The audit must
    say it checked no assets, not echo the flag back as though it had."""
    (tmp_path / "x.json").write_text(json.dumps(_item("x", FOREIGN_ID, ("a",))))
    proc = subprocess.run(
        [sys.executable, "scripts/register_manifest.py", "audit-items",
         "--dir", str(tmp_path), "--collection-id", FOREIGN_ID,
         "--forbid-asset", ","],
        cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "(no asset checks)" in proc.stderr, proc.stderr


def test_item_links_into_this_repos_bucket_make_it_this_repos_catalogue(tmp_path):
    """The fact that cannot be spelled around (code-check round 2). A copy of
    collection.json served from anywhere -- here file:// -- whose item links
    point into this repo's bucket, under an old id: the bucket URL names no S3
    bucket at all, so only the hrefs reveal whose items these are. Dryrun, so
    nothing is fetched from the real bucket; it exits after the rules are set."""
    items = [_item("d0", "some-old-id", (OLD_KEY,))]
    proc, writes = _run(tmp_path, "some-old-id", items,
                        stac_collection="some-old-id", href_base=PATH_S3_STAC,
                        args=("--all", "--dryrun"))
    out = _out(proc)
    assert proc.returncode == 0, out
    assert "asset audit: none" in proc.stdout           # before the fetch...
    forbid = ",".join(ASSET_RENAMES)
    assert (f"asset audit: require={ASSET_DEM} forbid={forbid}  "
            f"(1 item link(s) are in this repo's bucket)") in proc.stdout, out
    assert writes == 0


def test_item_links_into_this_repos_bucket_refuse_an_override(tmp_path):
    items = [_item("d0", "some-old-id", (OLD_KEY,))]
    proc, writes = _run(tmp_path, "some-old-id", items,
                        stac_collection="some-old-id", href_base=PATH_S3_STAC,
                        env_extra={"STAC_REQUIRE_ASSET": "thumbnail"})
    out = _out(proc)
    assert proc.returncode != 0
    assert "STAC_REQUIRE_ASSET" in out and "item link(s) point into" in out, out
    assert "item JSON(s)" not in proc.stdout             # refused before the fetch
    assert writes == 0


# =============================================================================
# s3_bucket_name -- what "this repo's bucket" is compared by
# =============================================================================

def test_the_own_bucket_constant_names_a_bucket():
    """The premise: if PATH_S3_STAC stopped parsing, every URL would compare
    'different' and this repo's catalogue would silently go foreign."""
    assert s3_bucket_name(PATH_S3_STAC) == "stac-dem-bc"


@pytest.mark.parametrize("url", OWN_BUCKET_ALIASES + [
    "s3://stac-dem-bc",
    "https://s3.amazonaws.com/stac-dem-bc/collection.json",
    "https://stac-dem-bc.s3.dualstack.us-west-2.amazonaws.com",
    "https://stac-dem-bc.s3-us-west-2.amazonaws.com",
])
def test_every_spelling_of_the_bucket_names_it(url):
    assert s3_bucket_name(url) == "stac-dem-bc"


@pytest.mark.parametrize("url", [
    "https://stac-airphoto-bc.s3.us-west-2.amazonaws.com",
    "https://stac-dem-bc-dev.s3.amazonaws.com",
    "https://evil.example/stac-dem-bc.s3.amazonaws.com",
    "https://stac-dem-bc.example.com",
])
def test_other_buckets_and_lookalikes_do_not(url):
    assert s3_bucket_name(url) != "stac-dem-bc"


@pytest.mark.parametrize("url", ["file:///tmp/bucket", "https://s3.amazonaws.com/",
                                 "not a url", ""])
def test_a_url_naming_no_bucket_is_none(url):
    """None, never "": two non-S3 URLs must not compare equal as buckets."""
    assert s3_bucket_name(url) is None


def test_a_dotted_bucket_name_survives():
    assert s3_bucket_name("https://my.dotted.bucket.s3.amazonaws.com") == "my.dotted.bucket"


def test_same_bucket_cli_answers_on_stdout():
    def run(a, b):
        return subprocess.run(
            [sys.executable, "scripts/register_manifest.py", "same-bucket", a, b],
            cwd=ROOT, capture_output=True, text=True, timeout=60)
    same = run("https://stac-dem-bc.s3.us-west-2.amazonaws.com", PATH_S3_STAC)
    diff = run("file:///x", "file:///x")
    assert (same.returncode, same.stdout.strip()) == (0, "same")
    # Two non-S3 URLs are not "the same bucket", however equal the strings.
    assert (diff.returncode, diff.stdout.strip()) == (0, "different")
