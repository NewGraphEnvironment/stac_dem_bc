# Code-check round 3: #40 repo rename (stac_dem_bc, the infrastructure PR, flooded#88, restoration_wedzin_kwa_2024#164, newgraphenvironment.github.io#60)

Reviewed 2026-10-17 against scratchpad/diff3.txt, the working tree at f3d77c7 (clean), the four live PR
bodies (identical to the saved copies apart from a trailing newline), issue #66, update.yml and
detect_changes.R, rtj CLAUDE.md on the rename branch, and the live GitHub API.

## The mechanism

All five earlier defects come from one cause. **The cutover is a single sequence, but it is held as
about a dozen hand-written restatements. Each one states a fact that is true at only one point in
that sequence, and none of them names the point.** The points are: before the apply, after the apply,
after the rename, after #60 deploys, after the local `mv`.

Two things then go wrong:

- **(a) A copy is not revised when the plan changes.** Examples: the cancel advice, the merge order,
  "would stop working" after the redirect was added, and the Phase 1 box.
- **(b) A copy becomes visible at a different point from the one it describes.** The rtj date is the
  example: it reached main before the rename it dated.

Round 2's enumeration checked a list of nine files that was itself picked by hand, so "all nine
agree" quantified over a set with members missing. The complete candidate set is every text whose
truth depends on cutover progress, across every claim class:

- the cutover order
- the old URL's fate
- tense ("renamed", "now", "since")
- handling of the confirming dispatch
- rollback scope
- merge gating
- the local clone path
- facts restated from other docs

Every member is checked below.

## Findings

- **[fragile]** `CLAUDE.md:386`: "Main repo: `/Users/airvine/Projects/repo/stac_elevation_bc` (the
  clone was `stac_dem_bc` until #40…)". This lands on main at cutover step 7. The `mv` that makes it
  true is a separate manual step afterwards (task_plan Phase 5), and it may never happen for the state host's clone.
  Until it does, the file every session here auto-loads names a directory that does not exist, and it
  says the clone moved "with #40" when #40's merge does not move it. This is class (b), the same shape
  as the rtj date. Fix: phrase it without a time anchor, e.g. "expected at …/stac_elevation_bc; a clone
  made before #40 is at …/stac_dem_bc until moved". Or do the `mv` right after the rename, before
  merging.

- **[fragile]** Rollback, in three places: pr40 body ("Rollback"), the infrastructure PR body ("Rollback"), and
  `planning/active/task_plan.md` Phase 3 last line. All three say: rename back, revert the infrastructure PR,
  re-apply. That is complete only up to step 6. Step 7 merges #40, #60, #88 and #164. A rollback after
  that leaves:
  - the org site home page, open-source page and table, flooded's README and wedzin's executive
    summary all pointing at `/stac_elevation_bc/`, which 404s once the repo is `stac_dem_bc` again;
  - NEWS, README and CLAUDE.md asserting the rename.

  This is the "two lists that agree by coincidence" shape: the merge list and the rollback list match
  only before step 7. Fix: say that the rollback applies through step 6, or list the four reverts for
  after step 7.

- **[fragile]** `planning/active/review-plan.md` G10 row: "cancel the confirming dispatch once
  `AWS credentials` is green | — | cutover step". The disposition column records the cancel advice as
  adopted. Round 1 overturned it (#66) and this row was not revised. Once archived, the plan review is
  the place someone looks for "what did we decide about the confirming run", and it gives the unsafe
  answer. This is class (a), the same as round 2's ticked box. The live instructions (both PR bodies
  and task_plan Phase 3) are correct. Fix: change the disposition to "rejected in round 1: let it
  finish (#66)".

- **[fragile]** `planning/active/task_plan.md` Phase 5 runs `/planning-archive` and `/gh-pr-push`
  before Phase 3. The cutover cannot start until the PR is open, and step 7 merges it. So the plan will
  be archived with every Phase 3 box unticked. The archive README's outcome paragraph will be written
  before the rename it records, and the Validation item "PWF checkboxes match landed work" cannot hold
  at archive time. This is class (b) again, for the archive record. Fix: either archive after the
  cutover, as a follow-up commit, or state in the archive README that Phase 3 is a post-PR handoff and
  record its outcome there once it runs.

- **[minor, accuracy]** Issue #66 quotes the commit step's condition as
  `always() && steps.detect.outputs.changes == 'true' || …`. The real condition
  (`update.yml:537-539`) is `always() && (changes == 'true' || footprints.pending || backfill || rename
  || footprint)`. As quoted, operator precedence gives the condition a different meaning. The issue's
  substance is correct:
  - `scripts/README.md:209` and `:214` say what it claims;
  - `detect_changes.R:141` rewrites `urls_list.txt` before the step runs;
  - the discard block covers only the three rewrite manifests.

  Fix: add the parentheses.

## Every place the mechanism reaches, checked

| text | claim class | verdict |
|---|---|---|
| the infrastructure PR body Order 1–4 | order, dispatch | consistent with pr40 and task_plan: plan/apply on the state host from branch → merge → rename → dispatch, let it finish. Cron "Nov 3 09:23 UTC" matches `'23 9 3 * *'`. `use_immutable_subject: false` matches round 2's API read. Saved plan under base airvine and apply under role-assume match rtj CLAUDE.md:336-350 |
| the infrastructure PR body Rollback | rollback | finding 2 |
| pr40 body steps 1–7 | order, dispatch, OIDC, Pages | consistent. The name `stac_elevation_bc` is free (API 404). "No releases" is true (`gh release list` empty). The step 4 field exists (`sub_claim_prefix`, round 2) |
| pr40 body Rollback | rollback | finding 2 |
| task_plan Phase 3 | order | consistent with both bodies; rollback line is finding 2 |
| task_plan Phase 5 / Validation | archive timing | finding 4 |
| flooded#88, wedzin#164 bodies | URL fate, merge gating | correct: old links redirect via #60; "until then the new URL is the one that 404s" |
| website#60 body | URL fate, merge gating, check | correct. The `curl -sL … \| grep -c stac_elevation_bc` check does discriminate: origin/main's index.html has 0 hits, so a stale project page fails it, and after the rename #40's page is served only at the new path |
| website `static/stac_dem_bc/index.html` | tense ("was renamed") | visible only after #60 deploys, which is after the rename. OK |
| NEWS Unreleased | tense, URL fate | true from step 7, apart from a minutes-long gap until #60 deploys. OK |
| CLAUDE.md:38 names table | tense | merges after the rename. OK |
| CLAUDE.md:386 File Locations | clone path | finding 1 |
| rtj main.tf comment, module README, stac-endpoints row, test_gh-auth.sh | tense | accepted tradeoff; the date is gone from stac-endpoints |
| findings.md "Order matters" (issue copy) | order | no merge or cancel step, so it contradicts nothing |
| findings.md round-2 section | "all nine agree" | a quantifier over a hand-picked list; the members it missed are findings 1–4 |
| review-plan.md | dispatch | finding 3 (G10). The other rows are consistent |
| review-round1/2.md | — | historical records of earlier states; their findings are marked as fixed elsewhere |
| progress.md | — | no state claims that conflict |
| issue #66 | facts restated from docs | finding 5 (quote only); the line citations are correct |
| issue #40 body | order, `update.yml:85` | stale line ref, already planned "fixed at merge"; its 3-step order has no cancel or merge advice |

## Checked and clean (beyond the table)

- **Remaining `stac_dem_bc` strings in this repo** are the `~/Projects/gis` data paths, CLAUDE.md
  history, NEWS's own rename text and the accepted test assertion. Excluded from the search:
  planning, logs, data, and the role ARN.
- **The clone path is not depended on.** No tracked file outside planning references
  `Projects/repo/stac_dem_bc`. `.claude/` has no references. soul's references are examples only.
  `.venv/bin/python` symlinks to uv's interpreter, so only the console-script shebangs hardcode the
  path, and recreating the venv covers them.
