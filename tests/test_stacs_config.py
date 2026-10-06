"""stacs.toml is this catalogue's declaration to stacs (#49), and a second copy of
values the modules already define.

Every value is pinned to its module here, because the whole point of the asset rules
is #34: a catalogue half `image` and half `dem` verifies IN SYNC by id set, and the
audit is the one thing that sees it. A toml that drifted from item_migrate.py -- a
second retired key added there and not here -- would stop checking it with nothing
failing.

The audit tests run the installed `stacs` CLI against items shaped by item_migrate, so
they prove the config is wired, not just present: drop `forbid` from stacs.toml and the
mixed-population test goes red.
"""

import copy
import importlib.metadata
import json
import os
import re
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import collection_patch  # noqa: E402
from item_migrate import ASSET_RENAMES, item_migrate  # noqa: E402
from stac_utils import ASSET_DEM, PATH_S3_STAC  # noqa: E402

# A hard import, not importorskip: a CI that forgot to install stacs must fail here,
# not skip the only tests that pin the catalogue's rules.
import stacs  # noqa: E402
from stacs.cli import ConfigError, main as stacs_main, read_config  # noqa: E402

CONFIG = os.path.join(ROOT, "stacs.toml")
STACS_VERSION = "0.1.0"
DEM = "https://nrs.objectstore.gov.bc.ca/gdwuts/082/082f/2022/dem/bc_082f005_xli1m_utm11_2022.tif"


@pytest.fixture(scope="module")
def cfg():
    # read_config refuses unknown tables and keys, so a typo is an error here rather
    # than a setting stacs silently ignores.
    return read_config(CONFIG)


# =============================================================================
# The toml agrees with the modules
# =============================================================================

def test_the_pinned_stacs_is_the_one_installed():
    """From the tag, not merely at its version: `__version__` is pyproject's, so an
    install from a later commit on main reads 0.1.0 too. direct_url.json records the
    revision pip/uv was asked for."""
    assert stacs.__version__ == STACS_VERSION
    direct = json.loads(importlib.metadata.distribution("stacs").read_text("direct_url.json"))
    assert direct["vcs_info"]["requested_revision"] == f"v{STACS_VERSION}"


def test_every_install_path_pins_the_same_tag():
    """The workflow and environment.yml each install stacs; a bump in one only would
    test one version in CI and run another on the laptop that registers."""
    pin = re.compile(r"stacs@v([0-9][0-9A-Za-z.\-]*[0-9A-Za-z])")
    for rel in (".github/workflows/update.yml", "environment.yml"):
        with open(os.path.join(ROOT, rel)) as fh:
            # Every pin in the file, whole: a substring test passed `v0.1.01`.
            assert pin.findall(fh.read()) == [STACS_VERSION], f"{rel} does not pin v{STACS_VERSION}"


def test_collection_id_is_the_modules(cfg):
    assert cfg["catalogue"]["collection_id"] == collection_patch.COLLECTION_ID


def test_bucket_url_is_the_modules(cfg):
    assert cfg["catalogue"]["bucket_url"] == PATH_S3_STAC


def test_required_asset_is_the_modules(cfg):
    assert cfg["assets"]["require"] == ASSET_DEM


def test_forbidden_assets_are_every_retired_key(cfg):
    """A set, so a key retired in item_migrate.py and not declared here fails."""
    assert set(cfg["assets"]["forbid"]) == set(ASSET_RENAMES)
    assert ASSET_DEM not in cfg["assets"]["forbid"]


def test_the_password_is_named_never_given(cfg, tmp_path):
    """The fence is stacs' refusal of unknown keys: a `password` line is an error,
    not a setting silently ignored. Proven on a copy of this file, so the fence is
    known to hold for this config rather than assumed from stacs' docs."""
    assert cfg["transport"]["password_env"] == "POSTGRES_PASSWORD"
    leaky = tmp_path / "stacs.toml"
    with open(CONFIG) as fh:
        leaky.write_text(fh.read().replace("[transport]\n", "[transport]\npassword = \"x\"\n", 1))
    # The refusal itself, not the bare word: every ConfigError carries the file path,
    # and pytest names tmp_path after this test, so "password" alone always matched.
    with pytest.raises(ConfigError, match=r"unknown key\(s\) in \[transport\]: password$"):
        read_config(leaky)


def test_the_unregister_script_targets_the_same_host_and_db(cfg):
    """collection_unregister.sh is the one host-side script stacs does not replace
    (stacs is upsert-only), so it keeps its own defaults -- pinned here, or a host
    move would leave the delete path pointing at the old machine."""
    with open(os.path.join(ROOT, "scripts", "collection_unregister.sh")) as fh:
        lines = [ln.strip() for ln in fh]
    # Every assignment, so a later `HOST=...` cannot override the pinned one unseen.
    assert [ln for ln in lines if ln.startswith("HOST=")] == \
        [f'HOST="${{STAC_HOST:-{cfg["transport"]["host"]}}}"']
    assert [ln for ln in lines if ln.startswith("DB=")] == \
        [f'DB="${{STAC_DB:-{cfg["transport"]["db"]}}}"']


# =============================================================================
# The rules as stacs applies them, over items shaped like ours
# =============================================================================

def _published(item_id):
    """A pre-rename item, as #34 found 102,460 of them."""
    return {
        "type": "Feature", "stac_version": "1.1.0", "id": item_id,
        "collection": "stac-dem-bc",
        "geometry": {"type": "Polygon", "coordinates": [[[0, 0]]]},
        "bbox": [0, 0, 1, 1],
        "properties": {"datetime": "2022-01-01T00:00:00Z"},
        "assets": {"image": {"href": DEM, "roles": ["data"]}},
        "links": [],
    }


def _migrated(item_id):
    it = _published(item_id)
    item_migrate(it)
    return it


class _Run:
    def __init__(self, returncode, stderr):
        self.returncode, self.stderr = returncode, stderr


def _audit(tmp_path, capsys, items, *extra):
    """`stacs audit --config stacs.toml`, in process: the CLI's own entry point,
    without depending on where the console script was installed."""
    d = tmp_path / "items"
    d.mkdir()
    for it in items:
        (d / f"{it['id']}.json").write_text(json.dumps(it))
    capsys.readouterr()
    rc = stacs_main(["audit", "--config", CONFIG, "--dir", str(d), *extra])
    return _Run(rc, capsys.readouterr().err)


def test_audit_passes_a_migrated_population(tmp_path, capsys):
    r = _audit(tmp_path, capsys, [_migrated(f"x{i}") for i in range(3)], "--expect", "3")
    assert r.returncode == 0, r.stderr
    assert f"require={ASSET_DEM}" in r.stderr


def test_audit_catches_one_stale_item_among_migrated_ones(tmp_path, capsys):
    """THE failure: the shape a reused manifest or an interrupted rewrite leaves."""
    # The stale item sits in the MIDDLE of the order stacs reads (sorted names): an
    # audit that checked only the last item read, or only the first, still fails here.
    items = [_migrated("a_good"), _published("m_stale"), _migrated("z_good")]
    r = _audit(tmp_path, capsys, items)
    assert r.returncode == 1
    # Counts, not just categories: "1 item(s)" says the stale one was flagged and
    # the good ones were not.
    assert "1 item(s) name another collection" in r.stderr
    assert "1 item(s) still carry a retired asset key" in r.stderr
    assert "m_stale" in r.stderr


def test_audit_catches_a_retired_key_even_in_the_right_collection(tmp_path, capsys):
    """Collection id alone cannot see half of #34: an item relabelled but not
    rewritten. Only the declared forbid rule catches it."""
    stale = _published("m_relabelled")
    stale["collection"] = collection_patch.COLLECTION_ID
    stale["assets"][ASSET_DEM] = copy.deepcopy(stale["assets"]["image"])
    r = _audit(tmp_path, capsys, [_migrated("a_good"), stale, _migrated("z_good")])
    assert r.returncode == 1
    assert "1 item(s) still carry a retired asset key" in r.stderr
    assert "name another collection" not in r.stderr


def test_a_flag_cannot_loosen_the_declared_rules(tmp_path, capsys):
    """Naming another collection does not drop the asset rules (#42's own-bucket test
    is gone; this is what replaces it)."""
    stale = _published("old")
    r = _audit(tmp_path, capsys, [stale], "--collection-id", "stac-dem-bc")
    assert r.returncode == 1
    assert "retired asset key" in r.stderr


def test_audit_refuses_a_count_short_of_the_publish(tmp_path, capsys):
    """The monthly step passes --expect from the publish count: a run that built a
    subset (a reused manifest) must fail here, not upload."""
    r = _audit(tmp_path, capsys, [_migrated(f"x{i}") for i in range(3)], "--expect", "4")
    assert r.returncode == 1
    assert "expected 4 item(s), audited 3" in r.stderr


def test_audit_refuses_an_empty_directory(tmp_path, capsys):
    r = _audit(tmp_path, capsys, [])
    assert r.returncode == 1
    assert "no item JSONs" in r.stderr
