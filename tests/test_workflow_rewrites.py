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


# Steps that name rewrite inputs for reasons other than "is this a rewrite run":
# the rebuild names `footprint` (its rewrite covers the footprint list) and
# `backfill` (it re-pairs), separately, and neither branch is about rename.
NOT_REWRITE_BRANCHES = {"Rebuild items whose DSM pairing or footprint changed"}


def test_every_shell_branch_naming_two_rewrites_names_all_three():
    for s in steps():
        if s.get("name") in NOT_REWRITE_BRANCHES:
            continue
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


def test_the_footprint_rebuild_list_is_pruned_by_staged_item_only_after_a_sync():
    commit = {s.get("name"): s for s in steps()}["Commit refreshed caches"]["run"]
    i = commit.index("--prune-changed")
    guard = commit[commit.rfind("if [", 0, i):i]
    assert 'steps.sync.outcome }}" = "success"' in guard
    assert ": > data/urls_footprint_changed.txt" not in commit     # never emptied wholesale


def test_footprints_run_every_run_and_pending_reaches_every_gate():
    by = {s.get("name"): s for s in steps()}
    assert by["Compute footprints for tiles not yet cached"]["if"] == "steps.detect.outcome == 'success'"
    for name in ("Rebuild items whose DSM pairing or footprint changed", "Count items to publish",
                 "Commit refreshed caches"):
        assert "steps.footprints.outputs.pending == 'true'" in by[name]["if"], name


def test_new_urls_are_passed_only_when_this_run_found_some():
    run = {s.get("name"): s for s in steps()}["Compute footprints for tiles not yet cached"]["run"]
    assert 'steps.detect.outputs.new_urls }}" = "true" ]; then NEW=(--new-urls data/urls_new.txt)' in run


def test_the_pairing_list_is_rebuilt_only_on_a_run_that_repaired():
    run = {s.get("name"): s for s in steps()}["Rebuild items whose DSM pairing or footprint changed"]["run"]
    i = run.index("LISTS+=(data/urls_pairing_changed.txt)")
    guard = run[run.rfind("if [", 0, i):i]
    assert "steps.detect.outputs.changes" in guard and "inputs.backfill" in guard
    assert "for f in data/urls_pairing_changed.txt" not in run


def test_monthly_validation_gate_is_a_full_pass_over_the_staged_set():
    run = {s.get("name"): s for s in steps()}["Validate new items (gate)"]["run"]
    monthly = run[run.index("else"):]
    first = monthly.index('--output "$RUNNER_TEMP/validation_staged.csv"')
    assert first < monthly.index('--items-dir "$STAC_OUTPUT_DIR" --incremental')


def test_the_footprint_list_is_not_rebuilt_on_dispatch_or_when_too_long():
    run = {s.get("name"): s for s in steps()}["Rebuild items whose DSM pairing or footprint changed"]["run"]
    add = run.index("LISTS+=(data/urls_footprint_changed.txt)")
    block = run[run.index('if [ "${{ inputs.footprint }}" = "true" ]'):add]
    assert '-gt 2000' in block and "elif" in block and "else" in block
