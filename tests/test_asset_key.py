"""One definition of the asset key, enforced structurally.

`image` -> `dem` (#34) had to move in four places at once: the cache path in
stac_utils (which builds nearly every item), item_create's rio_stac fallback and
its href override, and the same pair again in item_reprocess. A literal left in
any one of them is a half-done rename.

That failure is invisible to every runtime check this repo has. Item ids do not
change, so set equality still reports IN SYNC; both keys are legal STAC, so
validation passes; and a count of assets cannot tell {image, dsm} from
{dem, dsm}. It is also not reachable by a runtime test, because the two writers
are chosen by a cache hit and the miss branch reads a remote raster.

So the invariant is asserted over the SOURCE: no script may write an asset key
as a literal. That sweeps the whole of scripts/ rather than the call sites
someone remembered, and it cannot be gamed by fixture choice.
"""

import ast
import os
import sys

import pytest

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, SCRIPTS)

from stac_utils import ASSET_DEM, ASSET_DSM  # noqa: E402

# Keys that name an asset. A literal anywhere in an asset-writing position is
# the defect; a literal that is not one of these is some other dict and not our
# business.
ASSET_KEY_LITERALS = {"image", "dem", "dsm", "chm"}

# item_backfill is a recovery path over items published BEFORE #34, so it must
# be able to read the legacy `image` key as well as the current one. Its literal
# lives in one named constant (DEM_KEYS) with the reason beside it, and this
# test asserts that is still where it lives rather than exempting the file.
LEGACY_READER = "item_backfill.py"


def _script_files():
    return sorted(
        os.path.join(SCRIPTS, f)
        for f in os.listdir(SCRIPTS)
        if f.endswith(".py") and not f.startswith("_")
    )


def test_there_are_scripts_to_check():
    """The premise. A glob that silently matched nothing would pass every
    assertion below while checking nothing at all."""
    files = _script_files()
    assert len(files) >= 10
    assert any(f.endswith("stac_utils.py") for f in files)
    assert any(f.endswith("item_create.py") for f in files)


def _asset_key_literals(path):
    """Every literal asset key written in an asset-key POSITION, as (line, key).

    Three positions, matching the three ways this repo names an asset:
        item.add_asset("image", ...)        first positional argument
        create_stac_item(asset_name='image')  keyword
        item.assets['image']                 subscript
    """
    with open(path) as fh:
        tree = ast.parse(fh.read(), filename=path)

    found = []

    def literal(node):
        return (isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in ASSET_KEY_LITERALS)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name == "add_asset" and node.args and literal(node.args[0]):
                found.append((node.args[0].lineno, node.args[0].value))
            for kw in node.keywords:
                if kw.arg in ("asset_name", "asset_key") and literal(kw.value):
                    found.append((kw.value.lineno, kw.value.value))
        elif isinstance(node, ast.Subscript):
            v = node.value
            if isinstance(v, ast.Attribute) and v.attr == "assets" and literal(node.slice):
                found.append((node.slice.lineno, node.slice.value))
    return found


@pytest.mark.parametrize("path", _script_files(), ids=os.path.basename)
def test_no_script_writes_a_literal_asset_key(path):
    """The guard. Every asset key must come from stac_utils, not a string."""
    hits = _asset_key_literals(path)
    assert not hits, (
        f"{os.path.basename(path)} names an asset key as a literal at "
        f"{hits}. Use stac_utils.ASSET_DEM / ASSET_DSM — a literal here is "
        f"half a rename, and nothing downstream can see it."
    )


def test_the_scanner_can_actually_find_a_literal(tmp_path):
    """A scanner that finds nothing is indistinguishable from a clean tree.

    Written in all three positions the real scanner looks at, so a regression
    that drops one of the three branches is caught here rather than by shipping
    a guard that quietly stopped checking.
    """
    src = tmp_path / "bad.py"
    src.write_text(
        "item.add_asset('image', a)\n"
        "create_stac_item(x, asset_name='image')\n"
        "item.assets['image'].href = h\n"
    )
    hits = _asset_key_literals(str(src))
    assert [line for line, _ in hits] == [1, 2, 3]
    assert {key for _, key in hits} == {"image"}


def test_the_scanner_ignores_dicts_that_are_not_asset_keys(tmp_path):
    """It must not fire on a media type, a product token, or an unrelated dict.

    Without this the guard is unusable: PRODUCT_TOKENS in stac_utils and every
    'image/tiff; application=geotiff' would read as a violation, and the
    pressure would be to exempt files rather than fix them.
    """
    src = tmp_path / "fine.py"
    src.write_text(
        "MEDIA = 'image/tiff; application=geotiff'\n"
        "PRODUCT_TOKENS = ('dem', 'dsm', 'chm')\n"
        "row['dem'] = 1\n"
        "d = {'image': 2}\n"
        "item.add_asset(ASSET_DEM, a)\n"
        "item.assets[ASSET_DEM].href = h\n"
    )
    assert _asset_key_literals(str(src)) == []


def test_the_legacy_reader_keeps_its_literal_in_one_named_place():
    """item_backfill must still read pre-#34 items, and says so in one constant.

    This is not an exemption from the rule above — item_backfill passes that
    test. It is the assertion that the one legacy literal in the repo is where
    the comment says it is, so it cannot quietly spread back into the code.
    """
    import item_backfill
    assert item_backfill.DEM_KEYS == (ASSET_DEM, "image")
    assert item_backfill.DEM_KEYS[0] == ASSET_DEM, "current key must win"


# =============================================================================
# the workflows — scripts/ is not the boundary
# =============================================================================

WORKFLOWS = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows")

# The CLI flags through which a workflow can name an asset key.
ASSET_FLAGS = ("--require-asset", "--forbid-asset", "--asset-name")


def _workflow_files():
    if not os.path.isdir(WORKFLOWS):
        return []
    return sorted(os.path.join(WORKFLOWS, f) for f in os.listdir(WORKFLOWS)
                  if f.endswith((".yml", ".yaml")))


def _workflow_asset_literals(path):
    """(line, flag, value) wherever a workflow spells an asset key literally."""
    found = []
    with open(path) as fh:
        for n, line in enumerate(fh, 1):
            if line.lstrip().startswith("#"):
                continue
            for flag in ASSET_FLAGS:
                idx = line.find(flag)
                while idx != -1:
                    rest = line[idx + len(flag):].strip()
                    value = rest.split()[0].strip('"\'') if rest.split() else ""
                    # A shell variable is the correct form; a bare word is not.
                    if value and not value.startswith("$"):
                        found.append((n, flag, value))
                    idx = line.find(flag, idx + 1)
    return found


def test_there_are_workflows_to_check():
    """The premise. An empty listing would pass the guard below for nothing."""
    files = _workflow_files()
    assert any(f.endswith("update.yml") for f in files), \
        "the publishing workflow must be in scope"


@pytest.mark.parametrize("path", _workflow_files() or [None], ids=lambda p: os.path.basename(p or "none"))
def test_no_workflow_spells_an_asset_key(path):
    """update.yml drives every publish, so a literal there is exactly as wrong
    as one in scripts/ — and the AST scanner above cannot see YAML.

    It fails loudly rather than silently (a changed ASSET_DEM would make
    --require-asset reject every item), but it is the same one-fact-two-
    definitions this whole change removes.
    """
    if path is None:
        pytest.skip("no workflows in this repo")
    hits = _workflow_asset_literals(path)
    assert not hits, (
        f"{os.path.basename(path)} names an asset key literally at {hits}. "
        f"Read it from stac_utils.ASSET_DEM / item_migrate.ASSET_RENAMES."
    )


def test_the_workflow_scanner_can_find_a_literal(tmp_path):
    """A scanner that finds nothing is indistinguishable from a clean tree."""
    src = tmp_path / "probe.yml"
    src.write_text(
        "        # --require-asset dem  (a comment, must be ignored)\n"
        "        run: audit-items --require-asset dem --forbid-asset image\n"
        '        run: audit-items --require-asset "$DEM" --forbid-asset "$OLD"\n'
    )
    hits = _workflow_asset_literals(str(src))
    assert [(n, v) for n, _, v in hits] == [(2, "dem"), (2, "image")], hits


def test_the_two_asset_keys_are_distinct_and_named_for_the_product():
    assert ASSET_DEM == "dem"
    assert ASSET_DSM == "dsm"
    assert ASSET_DEM != ASSET_DSM
