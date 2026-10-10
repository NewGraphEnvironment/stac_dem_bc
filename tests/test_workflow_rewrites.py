"""Every place update.yml treats a one-time rewrite specially names all three (G4).

A rewrite dispatch (backfill #31, rename #34, footprint #2) is wired at a dozen
sites. Missing one has a recorded failure mode: the manifest-discard loop, if
it omits a manifest, lets a run whose sync failed commit ids it never
published, and they are skipped forever.
"""

import os

import yaml

WF = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows", "update.yml")
REWRITES = ("inputs.backfill", "inputs.rename", "inputs.footprint")
MANIFESTS = ("data/backfill_done.txt", "data/migrate_done.txt", "data/footprint_done.txt")


def steps():
    with open(WF) as fh:
        wf = yaml.safe_load(fh)
    return wf["jobs"]["update"]["steps"]


def test_footprint_is_a_dispatch_input():
    with open(WF) as fh:
        wf = yaml.safe_load(fh)
    on = wf.get("on", wf.get(True))       # YAML 1.1 reads a bare `on` as True
    assert "footprint" in on["workflow_dispatch"]["inputs"]


def test_every_condition_naming_two_rewrites_names_all_three():
    for s in steps():
        cond = str(s.get("if", ""))
        named = [r for r in REWRITES if r in cond]
        if len(named) >= 2:
            assert len(named) == 3, f"{s.get('name')}: {cond}"


def test_every_shell_branch_naming_two_rewrites_names_all_three():
    for s in steps():
        run = s.get("run", "")
        for line_block in run.split("\n          if ["):
            named = [r for r in REWRITES if r in line_block]
            if len(named) >= 2:
                assert len(named) == 3, f"{s.get('name')}: {line_block[:120]}"


def test_manifest_discard_and_artifacts_cover_every_manifest():
    by_name = {s.get("name"): s for s in steps()}
    commit = by_name["Commit refreshed caches"]["run"]
    artifacts = by_name["Upload run logs"]["with"]["path"]
    for m in MANIFESTS:
        assert m in commit, f"discard loop omits {m}"
        assert m in artifacts, f"artifact list omits {m}"


def test_the_footprint_rewrite_step_runs_only_on_dispatch():
    s = {s.get("name"): s for s in steps()}["Rewrite published items with footprints (dispatch only)"]
    assert s["if"] == "inputs.footprint"
    assert "--manifest data/footprint_done.txt" in s["run"]
