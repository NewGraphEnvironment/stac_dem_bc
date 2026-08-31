"""One definition of the collection id, and the bucket is not it.

#34 renames the collection `stac-dem-bc` -> `stac-elevation-bc` while the S3
BUCKET keeps the name `stac-dem-bc`. Those two used to be one fact spelled
twice, which made them safe to confuse; they are now genuinely two, and a
substitution that treated them as one would rewrite 102,460 item links to a
bucket that does not exist.

So there are two invariants here, and they point in opposite directions:

  the NEW id may appear in exactly one place under scripts/
  the OLD id may appear under scripts/ only inside a bucket URL

The second one IS the statement "the bucket is out of scope", written as
something that can fail.
"""

import ast
import io
import json
import os
import sys
import tokenize

import pytest

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, SCRIPTS)

import collection_patch  # noqa: E402
from collection_patch import collection_patch as _patch  # noqa: E402
from collection_patch import links_retitle  # noqa: E402
from stac_utils import PATH_S3_STAC  # noqa: E402

OLD_ID = "stac-dem-bc"

# The only forms in which the old string may survive: it is the bucket.
BUCKET_FORMS = (
    "stac-dem-bc.s3.amazonaws.com",
    "s3://stac-dem-bc",
    "arn:aws:s3:::stac-dem-bc",
)

# Repo and filesystem paths spell it with underscores, which is a different
# string and not this test's business.
assert "stac_dem_bc" != OLD_ID


WORKFLOWS = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows")


def _source_files():
    """Everything that could name a collection.

    The workflows are in scope deliberately. An inventory is only complete
    relative to a boundary, and scripts/ is not the boundary here: update.yml
    fetches the collection and drives every publish, so a hardcoded id there
    would be exactly as wrong and entirely invisible to a scan of scripts/.
    """
    out = []
    for name in sorted(os.listdir(SCRIPTS)):
        if name.endswith((".py", ".sh")) and not name.startswith("_"):
            out.append(os.path.join(SCRIPTS, name))
    if os.path.isdir(WORKFLOWS):
        for name in sorted(os.listdir(WORKFLOWS)):
            if name.endswith((".yml", ".yaml")):
                out.append(os.path.join(WORKFLOWS, name))
    return out


def test_there_are_source_files_to_check():
    """The premise. A listing that matched nothing would pass everything below."""
    files = _source_files()
    assert len(files) >= 15
    assert any(f.endswith("catalogue_register.sh") for f in files)
    assert any(f.endswith("collection_patch.py") for f in files)
    assert any(f.endswith("update.yml") for f in files), \
        "the publishing workflow must be in scope"


def _docstring_lines(src):
    """Line numbers occupied by docstrings — prose, never data.

    A docstring is discarded by the interpreter, so no live literal can hide in
    one: nothing can read it back. Every OTHER string is data and stays in
    scope. Found via the AST rather than by pattern, because a triple-quoted
    string is only a docstring by POSITION, and one assigned to a variable would
    look identical to any text-level scan.
    """
    lines = set()
    tree = ast.parse(src)
    holders = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    for node in ast.walk(tree):
        if not isinstance(node, holders):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return lines


def _code_lines(path):
    """(lineno, text) for lines that are CODE, with prose stripped.

    Comments and docstrings are removed rather than the file being skipped. A
    stale comment must not fail the test — but, the direction that actually
    matters, a live literal must not be able to hide by sitting next to one, so
    the removal is token- and AST-accurate rather than a text scan.
    """
    with open(path) as fh:
        src = fh.read()

    if path.endswith(".py"):
        prose = _docstring_lines(src)
        stripped = {}
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                continue
            if tok.start[0] in prose:
                continue
            if tok.string.strip():
                stripped.setdefault(tok.start[0], []).append(tok.string)
        return [(n, " ".join(parts)) for n, parts in sorted(stripped.items())]

    # Shell and YAML: strip whole-line comments only. An inline `#` inside a
    # string or a URL fragment cannot be removed by text alone, and cutting at
    # the first `#` would silently truncate a line that might carry the literal.
    out = []
    for n, line in enumerate(src.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        out.append((n, line))
    return out


def _hits(path, needle):
    return [(n, t.strip()) for n, t in _code_lines(path) if needle in t]


def test_the_scanner_strips_prose_but_not_data(tmp_path):
    """Both directions, because a scanner is only as good as what it still sees.

    Skipping docstrings is safe — the interpreter discards them, so nothing can
    read one back — but a triple-quoted string is a docstring only by POSITION.
    One assigned to a variable is data and must stay in scope, and that is
    exactly where a literal would hide from a text-level scan.
    """
    src = tmp_path / "probe.py"
    src.write_text(
        '"""A module docstring mentioning stac-dem-bc."""\n'
        "# a comment mentioning stac-dem-bc\n"
        'BUCKET = "stac-dem-bc"\n'
        'PROSE = """\n'
        "a triple-quoted string that is NOT a docstring: stac-dem-bc\n"
        '"""\n'
        "def f():\n"
        '    """A function docstring mentioning stac-dem-bc."""\n'
        "    return 1\n"
    )
    hits = _hits(str(src), OLD_ID)
    # A multi-line string is one token, reported at the line it STARTS on, so
    # the non-docstring string surfaces at its assignment (4) with its body
    # carried in the text.
    assert sorted(n for n, _ in hits) == [3, 4], f"got {hits}"
    assert any(OLD_ID in t for n, t in hits if n == 4), \
        "the body of a non-docstring string must stay in scope"


def test_the_scanner_finds_a_literal_in_a_shell_script(tmp_path):
    """Shell has no AST here, so its stripping is whole-line comments only —
    which means a literal on a code line is still caught, and that is the half
    that matters."""
    src = tmp_path / "probe.sh"
    src.write_text(
        "# a comment mentioning stac-dem-bc\n"
        'COLLECTION_ID="stac-dem-bc"\n'
    )
    assert [n for n, _ in _hits(str(src), OLD_ID)] == [2]


def test_the_new_collection_id_is_defined_in_exactly_one_place():
    """Every consumer reads collection_patch.COLLECTION_ID.

    A second definition would disagree with the first exactly once — during a
    rename, which is when being wrong is most expensive. That is the failure
    this repo already had: the id was a literal in collection_create.py AND in
    catalogue_register.sh.
    """
    defining = []
    for path in _source_files():
        if _hits(path, collection_patch.COLLECTION_ID):
            defining.append(os.path.basename(path))
    assert defining == ["collection_patch.py"], (
        f"{collection_patch.COLLECTION_ID} is written literally in {defining}. "
        f"It belongs in collection_patch.py alone; everything else imports "
        f"COLLECTION_ID or reads it at runtime."
    )


@pytest.mark.parametrize("path", _source_files(), ids=os.path.basename)
def test_every_remaining_old_id_in_scripts_is_a_bucket_url(path):
    """The bucket keeps its name, so the old string may only be the bucket.

    This is what makes "renaming the bucket is out of scope" checkable rather
    than a claim in a commit message. It also catches the dangerous direction of
    a bulk substitution: replacing the bucket in an item link would break every
    published href.
    """
    bad = [
        (n, t) for n, t in _hits(path, OLD_ID)
        if not any(form in t for form in BUCKET_FORMS)
    ]
    assert not bad, (
        f"{os.path.basename(path)} carries '{OLD_ID}' outside a bucket URL at "
        f"{bad}. The collection is now {collection_patch.COLLECTION_ID}; only "
        f"the bucket keeps the old name."
    )


def test_the_bucket_really_does_still_carry_the_old_name():
    """The premise of the test above, asserted rather than assumed.

    If the bucket were ever renamed too, that test would go on passing while
    checking nothing — every hit would simply disappear. Then this line fails
    and names the real cause.
    """
    assert OLD_ID in PATH_S3_STAC
    assert PATH_S3_STAC.startswith("https://stac-dem-bc.s3.")


def test_the_collection_id_and_the_bucket_are_not_the_same_string():
    """They were, until #34. Nothing may derive one from the other again."""
    assert collection_patch.COLLECTION_ID not in PATH_S3_STAC
    assert OLD_ID != collection_patch.COLLECTION_ID


def test_the_title_carries_the_id():
    assert collection_patch.COLLECTION_ID in collection_patch.COLLECTION_TITLE


def test_the_description_names_dem_and_not_image():
    """The backward-compatibility sentence expires with the rename.

    The published description had to explain that `image` was the bare-earth
    DEM. Once the key IS `dem`, that sentence is not merely redundant — it
    describes a catalogue that no longer exists.
    """
    d = collection_patch.DESCRIPTION
    assert "`dem` asset" in d
    assert "`dsm` asset" in d
    assert "image" not in d
    assert "backward compatibility" not in d


# =============================================================================
# collection_patch() — the id moves in all three places, and item links do not
# =============================================================================

BUCKET = PATH_S3_STAC


def _published(n_items=3, spacey=True):
    """A collection shaped like the real published one, pre-rename.

    Every property the rename could break has to be REACHABLE from this
    fixture, or the assertions below prove nothing:
      - the old id in all three places it is actually spelled
      - item links on the bucket, which must survive untouched
      - one link with a literal space, the only legitimate href change
    """
    old_title = f"Digital Elevation Models from British Columbia - {OLD_ID}"
    links = [{"rel": "root", "href": f"{BUCKET}/collection.json",
              "type": "application/json", "title": old_title}]
    for i in range(n_items):
        links.append({"rel": "item", "href": f"{BUCKET}/082-082e-2017-dem-x{i}.json"})
    if spacey:
        links.append({"rel": "item", "href": f"{BUCKET}/082-082e-2018-dem-x 1.json"})
    return {"type": "Collection", "id": OLD_ID, "title": old_title, "links": links}


def test_the_fixture_can_reach_every_outcome():
    """Assert the premises inline. A fixture missing any of these would let the
    tests below pass without exercising what they name."""
    c = _published()
    assert c["id"] == OLD_ID
    assert OLD_ID in c["title"]
    root = [l for l in c["links"] if l["rel"] == "root"][0]
    assert OLD_ID in root["title"], "root link must carry the old title"
    items = [l for l in c["links"] if l["rel"] == "item"]
    assert all(BUCKET in l["href"] for l in items), "item links must be on the bucket"
    assert any(" " in l["href"] for l in items), "one link must carry a raw space"


def test_patch_moves_the_id_in_all_three_places():
    c, changed = _patch(_published())
    assert c["id"] == collection_patch.COLLECTION_ID
    assert c["title"] == collection_patch.COLLECTION_TITLE
    root = [l for l in c["links"] if l["rel"] == "root"][0]
    assert root["title"] == collection_patch.COLLECTION_TITLE
    assert "id" in changed and "title" in changed and "root link title" in changed


def test_patch_leaves_the_root_link_href_on_the_bucket():
    """The root link's TITLE names the collection; its HREF names the bucket.

    They are different facts sharing one line of JSON, which is exactly the
    shape a careless substitution gets wrong.
    """
    c, _ = _patch(_published())
    root = [l for l in c["links"] if l["rel"] == "root"][0]
    assert root["href"] == f"{BUCKET}/collection.json"
    assert OLD_ID in root["href"]


def test_patch_does_not_move_item_links_off_the_bucket():
    """102,460 hrefs point at a bucket that keeps its name. Renaming them would
    break every published item and could not be undone from the API."""
    c, _ = _patch(_published(n_items=5))
    for link in [l for l in c["links"] if l["rel"] == "item"]:
        assert link["href"].startswith(BUCKET), link["href"]
        assert collection_patch.COLLECTION_ID not in link["href"]


def test_patch_still_encodes_spaces_and_changes_nothing_else_in_hrefs():
    before = [l["href"] for l in _published()["links"] if l["rel"] == "item"]
    c, _ = _patch(_published())
    after = [l["href"] for l in c["links"] if l["rel"] == "item"]
    assert len(after) == len(before)
    assert after[-1] == before[-1].replace(" ", "%20")
    assert after[:-1] == before[:-1]


def test_patch_is_idempotent_after_the_rename():
    """The contract --check reports on. A second run must report nothing."""
    c, first = _patch(_published())
    assert first, "the first run must have something to do"
    _, second = _patch(c)
    assert second == []


def test_patch_of_an_already_renamed_collection_reports_no_change():
    c = _published()
    _patch(c)
    # Through JSON, so the second run sees a document rather than the same
    # objects the first run mutated in place.
    fresh = json.loads(json.dumps(c))
    _, changed = _patch(fresh)
    assert changed == []


def test_links_retitle_touches_only_the_root_link():
    c = _published()
    n = links_retitle(c, "NEW")
    assert n == 1
    assert [l for l in c["links"] if l["rel"] == "root"][0]["title"] == "NEW"
    assert all("title" not in l for l in c["links"] if l["rel"] == "item")


def test_links_retitle_is_idempotent():
    c = _published()
    assert links_retitle(c, "NEW") == 1
    assert links_retitle(c, "NEW") == 0


def test_links_retitle_adds_a_title_to_a_root_link_that_has_none():
    """pystac writes one, but a hand-built or older file may not carry it.
    Silently leaving it absent would mean the id is spelled twice, not three
    times, and the next patch would look idempotent while being incomplete."""
    c = {"links": [{"rel": "root", "href": f"{BUCKET}/collection.json"}]}
    assert links_retitle(c, "NEW") == 1
    assert c["links"][0]["title"] == "NEW"
