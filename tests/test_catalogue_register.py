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
import threading
import urllib.parse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
# The probe is `ssh -o ... HOST true`; anything else is a write. Each write is
# logged as "<kind> <lines received>", so a test can see WHAT was loaded and
# how much of it -- not just that something was. Fails every write unless
# SSH_STUB_OK is set.
last="${@: -1}"
if [ "$last" = "true" ]; then exit 0; fi
n=$(wc -l | tr -d ' ')
kind=other
case "$last" in
  *"load items"*) kind=items ;;
  *"load collections"*) kind=collections ;;
esac
echo "$kind $n" >> "$SSH_LOG"
if [ -n "${SSH_STUB_OK:-}" ]; then exit 0; fi
exit 1
"""


def _loads(tmp_path):
    """The ssh stub's log: [(kind, lines received)] per attempted write."""
    log = tmp_path / "ssh.log"
    if not log.exists():
        return []
    return [(k, int(n)) for k, n in
            (line.split() for line in log.read_text().splitlines())]

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


def _collection(collection_id, links=()):
    return {"type": "Collection", "id": collection_id, "stac_version": "1.0.0",
            "description": "fixture", "license": "proprietary",
            "extent": {}, "links": list(links)}


def _run(tmp_path, collection_id, items, env_extra=None, args=("--all",),
         stac_collection=None, href_base=None, api=None, before=None):
    """Publish `items` under `collection_id` as file:// and run the script.

    `api` is a stub API's base URL (see `stub_api`); by default the API is an
    unreachable port. `before(bucket)` runs after publishing, to break it.

    Returns (CompletedProcess, number of attempted writes).
    """
    bucket = tmp_path / "bucket"
    bucket.mkdir(exist_ok=True)
    links = []
    for doc in items:
        p = bucket / f"{doc['id']}.json"
        p.write_text(json.dumps(doc))
        href = f"{href_base}/{doc['id']}.json" if href_base else p.as_uri()
        links.append({"rel": "item", "href": href,
                      "type": "application/json"})
    (bucket / "collection.json").write_text(json.dumps(
        _collection(collection_id, links)))
    if before is not None:
        before(bucket)

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
              "STAC_BUCKET_URL", "STAC_API", "STAC_HOST", "STAC_DB",
              "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "no_proxy", "NO_PROXY", "all_proxy", "ALL_PROXY"):
        env.pop(k, None)
    env.update({
        "PATH": f"{bindir}{os.pathsep}{env['PATH']}",
        "SSH_LOG": str(log),
        "STAC_HOST": "nobody@stub.invalid",
        "STAC_BUCKET_URL": bucket.as_uri(),
        # Unreachable, so the post-registration verify can never touch the
        # real API. Nothing here should get that far anyway.
        "STAC_API": api or "http://127.0.0.1:9",
        # Network-proof for the Python side, as CURL_STUB is for curl: requests
        # and urllib both honour the proxy variables, so any http(s) request the
        # fetcher or the API client makes goes to a dead port -- except to
        # localhost, where the stub API lives. A harness bug that pointed a run
        # at the real bucket fails every fetch (after its retries) instead of
        # fetching 102k items. Pinned by test_the_harness_is_network_proof.
        "http_proxy": "http://127.0.0.1:9",
        "https_proxy": "http://127.0.0.1:9",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "no_proxy": "127.0.0.1,localhost",
        "NO_PROXY": "127.0.0.1,localhost",
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


# =============================================================================
# Content drift (#45): the ids match, the bodies do not
# =============================================================================
#
# A stub STAC API on localhost, speaking just enough of stac-fastapi for the
# script: POST /search (collections, ids, limit, fields, keyset token) and
# GET /collections/<id>. What it serves is what the test says is "registered".

class _StubAPI:
    def __init__(self):
        self.items = {}          # id -> registered body
        self.collection = None   # registered collection body, or None (404)
        self.searches = []
        # Set both to make the stub react to a write: once the ssh stub's log
        # shows a collection load, `on_load(self)` runs once -- standing in for
        # what pgstac would then serve.
        self.load_log = None
        self.on_load = None

    def sync(self):
        if self.on_load and self.load_log and self.load_log.exists() and \
                "collections" in self.load_log.read_text():
            fn, self.on_load = self.on_load, None
            fn(self)


def _handler(api):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, payload):
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            api.sync()
            prefix = "/collections/"
            if self.path.startswith(prefix) and "/" not in self.path[len(prefix):]:
                c = api.collection
                if c is not None and c["id"] == self.path[len(prefix):]:
                    self._send(200, {**c, "links": [{"rel": "self", "href": "x"}]})
                    return
            self._send(404, {"code": "NotFoundError"})

        def do_POST(self):
            if self.path != "/search":
                self._send(404, {})
                return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            api.sync()
            api.searches.append(body)
            docs = [d for d in api.items.values()
                    if d.get("collection") in body.get("collections", [])]
            if "ids" in body:
                docs = [d for d in docs if d["id"] in set(body["ids"])]
            docs.sort(key=lambda d: d["id"])
            limit = body.get("limit", 10)
            start = int(body.get("token") or 0)
            page = docs[start:start + limit]
            if body.get("fields", {}).get("include") == ["id"]:
                page = [{"id": d["id"]} for d in page]
            else:
                # The API adds its own links; the comparison must ignore them.
                page = [{**d, "links": [{"rel": "self", "href": "x"}]} for d in page]
            links = []
            if start + limit < len(docs):
                links.append({"rel": "next", "body": {"token": str(start + limit)}})
            self._send(200, {"type": "FeatureCollection", "features": page,
                             "links": links})
    return H


@pytest.fixture
def stub_api():
    api = _StubAPI()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(api))
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    api.url = f"http://127.0.0.1:{server.server_address[1]}"
    yield api
    server.shutdown()
    server.server_close()


def _in_sync(api, collection_id, items):
    api.collection = _collection(collection_id)
    api.items = {d["id"]: json.loads(json.dumps(d)) for d in items}


def _foreign_items(n=3):
    return [_item(f"a{i}", FOREIGN_ID, FOREIGN_ASSETS) for i in range(n)]


def test_verify_reports_in_sync_when_bodies_match(tmp_path, stub_api):
    """Control: without it, every "changed" assertion below could pass on a
    comparison that never matches anything."""
    items = _foreign_items()
    _in_sync(stub_api, FOREIGN_ID, items)
    proc, writes = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID,
                        api=stub_api.url, args=("--verify",))
    out = _out(proc)
    assert proc.returncode == 0, out
    assert "IN SYNC" in out, out
    assert writes == 0


def test_verify_reports_an_item_whose_body_changed(tmp_path, stub_api):
    """The #45 case: same ids on both sides, one body rewritten since it was
    registered. Id-set equality said IN SYNC here."""
    items = _foreign_items()
    _in_sync(stub_api, FOREIGN_ID, items)
    stub_api.items["a1"]["properties"] = {"file:checksum": "stale"}
    proc, writes = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID,
                        api=stub_api.url, args=("--verify",))
    out = _out(proc)
    assert proc.returncode == 1, out
    assert "IN SYNC" not in out
    assert "1 registered item(s) differ from the published body" in out, out
    assert "changed:  a1" in out, out
    assert writes == 0


def test_verify_reports_a_collection_whose_body_changed(tmp_path, stub_api):
    """A version bump with no item change is the same defect, one object up."""
    items = _foreign_items()
    _in_sync(stub_api, FOREIGN_ID, items)
    stub_api.collection["description"] = "the old description"
    proc, _ = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID,
                   api=stub_api.url, args=("--verify",))
    out = _out(proc)
    assert proc.returncode == 1, out
    assert "collection body differs" in out, out
    assert "IN SYNC" not in out


def test_verify_reports_a_collection_that_is_not_registered(tmp_path, stub_api):
    items = _foreign_items()
    _in_sync(stub_api, FOREIGN_ID, items)
    stub_api.collection = None
    proc, _ = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID,
                   api=stub_api.url, args=("--verify",))
    out = _out(proc)
    assert proc.returncode == 1, out
    assert "collection is not registered" in out, out


def test_verify_fails_when_a_published_body_cannot_be_fetched(tmp_path, stub_api):
    """The failure the issue names: a body that cannot be read must fail the
    run, not compare as unchanged. The removed spot-check passed on null == null."""
    items = _foreign_items()
    _in_sync(stub_api, FOREIGN_ID, items)
    proc, writes = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID,
                        api=stub_api.url, args=("--verify",),
                        before=lambda bucket: (bucket / "a2.json").unlink())
    out = _out(proc)
    assert proc.returncode != 0, out
    assert "IN SYNC" not in out
    assert "fetched 2 of 3" in out, out
    assert writes == 0


def test_drift_dryrun_lists_a_changed_item(tmp_path, stub_api):
    items = _foreign_items()
    _in_sync(stub_api, FOREIGN_ID, items)
    stub_api.items["a0"]["assets"] = {}
    proc, writes = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID,
                        api=stub_api.url, args=("--drift", "--dryrun"))
    out = _out(proc)
    assert proc.returncode == 0, out
    assert "to register: 1" in proc.stdout, out
    assert "a0" in proc.stdout, out
    assert writes == 0


def test_drift_registers_missing_and_changed_together(tmp_path, stub_api):
    """One missing, one changed: both go in the todo set, and the run proceeds
    to the write (which the ssh stub fails -- one attempted write)."""
    items = _foreign_items()
    _in_sync(stub_api, FOREIGN_ID, items)
    del stub_api.items["a2"]
    stub_api.items["a0"]["properties"] = {"x": 1}
    proc, writes = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID,
                        api=stub_api.url, args=("--drift",))
    out = _out(proc)
    assert "to register: 2" in proc.stdout, out
    assert "checked 2 item(s)" in proc.stderr, out
    assert writes == 1, out


def test_drift_in_sync_does_nothing(tmp_path, stub_api):
    items = _foreign_items()
    _in_sync(stub_api, FOREIGN_ID, items)
    proc, writes = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID,
                        api=stub_api.url, args=("--drift",))
    out = _out(proc)
    assert proc.returncode == 0, out
    assert "already in sync" in proc.stdout, out
    assert writes == 0


def test_drift_upserts_a_changed_collection_with_no_item_changes(tmp_path, stub_api):
    items = _foreign_items()
    _in_sync(stub_api, FOREIGN_ID, items)
    stub_api.collection["license"] = "CC-BY-4.0"
    proc, writes = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID,
                        api=stub_api.url, args=("--drift",))
    out = _out(proc)
    assert "to register: 0" in proc.stdout, out
    assert "already in sync" not in proc.stdout, out
    assert writes == 1, out    # the collection upsert, failed by the stub


def test_drift_loads_only_the_todo_bodies(tmp_path, stub_api):
    """--drift fetches the WHOLE catalogue to compare it. What it hands the
    loader must be the todo set -- here 2 of 4 -- not everything it fetched.
    Writes succeed in this run, so the item load is observable."""
    items = _foreign_items(4)
    _in_sync(stub_api, FOREIGN_ID, items)
    del stub_api.items["a3"]
    stub_api.items["a0"]["properties"] = {"x": 1}
    proc, _ = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID,
                   api=stub_api.url, args=("--drift",),
                   env_extra={"SSH_STUB_OK": "1"})
    out = _out(proc)
    assert _loads(tmp_path) == [("collections", 1), ("items", 2)], out
    # The stub API did not take the load, so the post-register check sees the
    # old body and the missing item -- and must fail, not report DONE. This is
    # the guard against a --drift that re-registers forever without converging.
    assert proc.returncode != 0, out
    assert "DONE" not in proc.stdout
    assert "not served" in out and "differs from the one registered" in out, out


def test_drift_bootstraps_a_collection_the_api_has_never_seen(tmp_path, stub_api):
    """First registration of a collection: /collections/<id> is 404 and /search
    returns nothing. That must read as 'register everything', not as an error."""
    items = _foreign_items(3)
    proc, _ = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID,
                   api=stub_api.url, args=("--drift",),
                   env_extra={"SSH_STUB_OK": "1"})
    out = _out(proc)
    assert "to register: 3" in proc.stdout, out
    assert _loads(tmp_path) == [("collections", 1), ("items", 3)], out


def test_an_id_with_spaces_and_parentheses_round_trips(tmp_path, stub_api):
    """90 published ids carry literal spaces and parentheses (#25), and their
    hrefs encode spaces only. A fetcher that mishandled either would fetch a
    body that names a different id, or none."""
    from stac_utils import encode_url_for_gdal
    items = [_item("a (2) b", FOREIGN_ID, FOREIGN_ASSETS),
             _item("plain", FOREIGN_ID, FOREIGN_ASSETS)]
    _in_sync(stub_api, FOREIGN_ID, items)
    stub_api.items["a (2) b"]["properties"] = {"x": 1}

    def reencode(bucket):
        # Rewrite the links the way the real catalogue spells them.
        c = json.loads((bucket / "collection.json").read_text())
        for link in c["links"]:
            if link.get("rel") == "item":
                name = link["href"].rsplit("/", 1)[-1]
                name = urllib.parse.unquote(name)
                link["href"] = "file://" + encode_url_for_gdal(str(bucket / name))
        (bucket / "collection.json").write_text(json.dumps(c))

    proc, _ = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID,
                   api=stub_api.url, args=("--verify",), before=reencode)
    out = _out(proc)
    assert proc.returncode == 1, out
    assert "changed:  a (2) b" in out, out
    assert "1 registered item(s) differ" in out, out


def test_the_harness_is_network_proof(tmp_path):
    """The proxy variables are what keep the Python fetcher off the network in
    these tests. A fetcher built with trust_env=False or an empty ProxyHandler
    would silently turn that off; this is the check that it has not."""
    urls = tmp_path / "urls.txt"
    urls.write_text("https://stac-dem-bc.s3.amazonaws.com/collection.json\n")
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("no_proxy", "http_proxy", "https_proxy", "all_proxy")}
    env.update({"https_proxy": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
                "http_proxy": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9"})
    proc = subprocess.run(
        [sys.executable, "scripts/register_manifest.py", "fetch-bodies",
         "--urls-file", str(urls), "--out-dir", str(tmp_path),
         "--failed-out", str(tmp_path / "failed.txt"), "--workers", "1"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert (tmp_path / "failed.txt").read_text().strip().endswith("collection.json"), proc.stderr
    assert not list(tmp_path.glob("????????????????????????????????.json"))


# =============================================================================
# verify-serving -- the post-register content check, on its own
# =============================================================================

def _verify_serving(tmp_path, api, docs, ids):
    bucket = tmp_path / "vs"
    bucket.mkdir()
    fetch = tmp_path / "vs_items"
    fetch.mkdir()
    rows = []
    for d in docs:
        p = bucket / f"{d['id']}.json"
        p.write_text(json.dumps(d))
        href = p.as_uri()
        (fetch / f"{hashlib.md5(href.encode()).hexdigest()}.json").write_text(json.dumps(d))
        rows.append(f"{d['id']}\t{href}\n")
    (tmp_path / "hrefs.tsv").write_text("".join(rows))
    (tmp_path / "ids.txt").write_text("".join(f"{i}\n" for i in ids))
    return subprocess.run(
        [sys.executable, "scripts/register_manifest.py", "verify-serving",
         "--ids-file", str(tmp_path / "ids.txt"), "--collection-id", FOREIGN_ID,
         "--api", api, "--hrefs-file", str(tmp_path / "hrefs.tsv"),
         "--fetch-dir", str(fetch)],
        cwd=ROOT, capture_output=True, text=True, timeout=60)


def test_verify_serving_passes_when_the_served_body_is_the_sent_one(tmp_path, stub_api):
    docs = _foreign_items(3)
    _in_sync(stub_api, FOREIGN_ID, docs)
    proc = _verify_serving(tmp_path, stub_api.url, docs, ["a0", "a2"])
    assert proc.returncode == 0, proc.stderr
    assert "with the body that was registered" in proc.stderr


def test_verify_serving_fails_when_a_served_body_differs(tmp_path, stub_api):
    docs = _foreign_items(3)
    _in_sync(stub_api, FOREIGN_ID, docs)
    stub_api.items["a2"]["assets"] = {}
    proc = _verify_serving(tmp_path, stub_api.url, docs, ["a0", "a2"])
    assert proc.returncode == 1, proc.stderr
    assert "differs from the one registered" in proc.stderr
    assert "'a2'" in proc.stderr


def test_verify_serving_fails_when_an_id_is_not_served(tmp_path, stub_api):
    docs = _foreign_items(3)
    _in_sync(stub_api, FOREIGN_ID, docs)
    del stub_api.items["a1"]
    proc = _verify_serving(tmp_path, stub_api.url, docs, ["a0", "a1"])
    assert proc.returncode == 1, proc.stderr
    assert "not served" in proc.stderr


def test_verify_serving_asks_for_full_bodies_with_a_limit(tmp_path, stub_api):
    """The default limit is 10 and an id-only body digests to nothing real."""
    docs = [_item(f"a{i:02d}", FOREIGN_ID, FOREIGN_ASSETS) for i in range(12)]
    _in_sync(stub_api, FOREIGN_ID, docs)
    proc = _verify_serving(tmp_path, stub_api.url, docs, [d["id"] for d in docs])
    assert proc.returncode == 0, proc.stderr
    assert stub_api.searches[-1]["limit"] == 12
    assert "fields" not in stub_api.searches[-1]


# =============================================================================
# After the write (code-check round 2)
# =============================================================================

def test_a_duplicated_item_link_is_refused_before_any_write(tmp_path, stub_api):
    """An identical href twice collapses under sort -u. --all used to register
    everything and then crash in verify-serving on every rerun."""
    items = _foreign_items(2)

    def dup(bucket):
        c = json.loads((bucket / "collection.json").read_text())
        c["links"].append(next(l for l in c["links"] if l["rel"] == "item"))
        (bucket / "collection.json").write_text(json.dumps(c))

    for args in (("--all",), ("--drift",), ("--verify",)):
        run_dir = tmp_path / args[0].strip("-")
        run_dir.mkdir()
        proc, writes = _run(run_dir, FOREIGN_ID, items, stac_collection=FOREIGN_ID,
                            api=stub_api.url, args=args, before=dup,
                            env_extra={"SSH_STUB_OK": "1"})
        out = _out(proc)
        assert proc.returncode != 0, (args, out)
        assert "links an item more than once" in out, (args, out)
        assert "fetching" not in proc.stdout.split("published  :")[-1], (args, out)
        assert writes == 0, (args, out)


def test_all_succeeds_end_to_end_when_the_api_serves_what_was_sent(tmp_path, stub_api):
    """The positive path, which no other test reaches: writes succeed, the API
    serves the published bodies, and the run says DONE and exits 0."""
    items = _foreign_items(3)
    _in_sync(stub_api, FOREIGN_ID, items)
    proc, _ = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID,
                   api=stub_api.url, args=("--all",),
                   env_extra={"SSH_STUB_OK": "1"})
    out = _out(proc)
    assert proc.returncode == 0, out
    assert _loads(tmp_path) == [("collections", 1), ("items", 3)], out
    assert "re-comparing every published body" in proc.stdout, out
    assert "OK: every published item is served with the published body" in proc.stdout
    assert "DONE: 3 item(s)" in proc.stdout


def test_drift_rechecks_untouched_items_when_the_collection_changed(tmp_path, stub_api):
    """pgstac serves items hydrated against their collection, so a collection
    upsert can change how items NOT in the todo set read back. The run that
    caused it must see it, not the next --verify."""
    items = _foreign_items(3)
    _in_sync(stub_api, FOREIGN_ID, items)
    stub_api.collection["license"] = "old-license"
    stub_api.items["a0"]["properties"] = {"x": 1}

    def after(api):
        api.collection = _collection(FOREIGN_ID)
        api.items["a0"] = json.loads(json.dumps(items[0]))
        api.items["a2"]["properties"] = {"hydrated": "differently"}  # untouched

    stub_api.load_log, stub_api.on_load = tmp_path / "ssh.log", after
    proc, _ = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID,
                   api=stub_api.url, args=("--drift",),
                   env_extra={"SSH_STUB_OK": "1"})
    out = _out(proc)
    assert _loads(tmp_path) == [("collections", 1), ("items", 1)], out
    assert proc.returncode != 0, out
    assert "re-comparing every published body" in proc.stdout, out
    assert "changed:  a2" in out, out
    assert "DONE" not in proc.stdout


def test_drift_with_a_changed_collection_passes_when_items_read_back_unchanged(
        tmp_path, stub_api):
    """Control for the test above: the full re-check is not merely always red."""
    items = _foreign_items(3)
    _in_sync(stub_api, FOREIGN_ID, items)
    stub_api.collection["license"] = "old-license"

    def after(api):
        api.collection = _collection(FOREIGN_ID)

    stub_api.load_log, stub_api.on_load = tmp_path / "ssh.log", after
    proc, _ = _run(tmp_path, FOREIGN_ID, items, stac_collection=FOREIGN_ID,
                   api=stub_api.url, args=("--drift",),
                   env_extra={"SSH_STUB_OK": "1"})
    out = _out(proc)
    assert proc.returncode == 0, out
    assert _loads(tmp_path) == [("collections", 1)], out
    assert "OK: every published item is served with the published body" in proc.stdout
    assert "DONE: 0 item(s)" in proc.stdout


def test_a_body_naming_another_id_is_refused_before_any_write(tmp_path, stub_api):
    """Code-check round 3. item_register.sh upserts by the body's OWN id, so a
    link a1.json serving a body that names a0 would overwrite the registered a0
    under --all/--ids-file -- and was only noticed after the write. The check
    that catches it has to run before the loader reads anything, in every mode."""
    items = _foreign_items(3)
    _in_sync(stub_api, FOREIGN_ID, items)

    def swap(bucket):
        (bucket / "a1.json").write_text(json.dumps(items[0]))

    for args in (("--all",), ("--drift",)):
        run_dir = tmp_path / args[0].strip("-")
        run_dir.mkdir()
        proc, writes = _run(run_dir, FOREIGN_ID, items, stac_collection=FOREIGN_ID,
                            api=stub_api.url, args=args, before=swap,
                            env_extra={"SSH_STUB_OK": "1"})
        out = _out(proc)
        assert proc.returncode != 0, (args, out)
        assert "names id 'a0'" in out, (args, out)
        assert writes == 0, (args, out)

    ids = tmp_path / "ids.txt"
    ids.write_text("a1\n")
    run_dir = tmp_path / "ids-file"
    run_dir.mkdir()
    proc, writes = _run(run_dir, FOREIGN_ID, items, stac_collection=FOREIGN_ID,
                        api=stub_api.url, args=("--ids-file", str(ids)),
                        before=swap, env_extra={"SSH_STUB_OK": "1"})
    out = _out(proc)
    assert proc.returncode != 0, out
    assert "names id 'a0'" in out, out
    assert writes == 0, out
