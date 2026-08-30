# Progress — Add `dsm` as a second asset on each item (#31)

## Session 2026-08-28

- Plan-mode exploration: read `item_create.py`, `stac_utils.py`, `collection_create.py`,
  `urls_fetch.R`, `detect_changes.R`, `update.yml`, `s3_sync-ci.sh`; probed the live
  objectstore, the S3 collection, and the STAC API
- Established that the issue's 60,126 figure is the pgstac registration count, not the
  catalog (98,040 item links on S3) — scope of the "catch up" bullet reduced accordingly
- Three decisions taken with the user: reconcile the ~2.2k real DEM gap only (pgstac
  stays with #27); inherit DSM media type from the paired DEM with sample verification;
  add `dsm` while leaving `image` and the `-dem-` item id alone
- Created branch `31-add-dsm-as-a-second-asset-on-each-item` off main
- Scaffolded PWF baseline with the approved phases
- Next: Phase 1 — reconcile the DEM listing gap

### Phases 1-4, 7 (partial) — 2026-08-28

- Reconciled the DEM gap: an independent full bucket walk (575,411 keys) reproduces
  #29 exactly — 100,171 DEM `.tif`, 95,889 DSM `.tif`, 264 CHM, 15,752 orthophoto.
  **The listing filter was not at fault**: `pattern = c("dem", "*.tif")` yields
  102,416 = 100,171 under `dem/` + 2,245 `albers10k2m/_completed_dem/`, exactly
  accounted for. The shortfall is 4,420 genuinely new DEM tiles arrived since the
  2026-08-03 run. `detect_changes.R` confirmed: 4,420 new, 0 deleted.
- Restructured listing into `scripts/urls_listing.R`: ONE bucket walk yields DEM
  keys, DSM keys and `dsm/` directory membership. The third artifact is what makes
  a `.laz`-only delivery a declared gap rather than indistinguishable from "no DSM".
- `source()` moved inside `detect_changes.R`'s tryCatch — at top level a broken
  helper would abort with R's default status 1, which the workflow reads as
  "changes detected" rather than "error".
- Pairing reconciles with #29 to the tile:

  | outcome | tiles | #29 |
  |---|---|---|
  | paired, convention `suffix` | 95,768 | 95,768 |
  | paired, convention `identical` | 117 | 117 |
  | paired, convention `unknown` | 2 | — (new) |
  | `no_raster_dsm` (11 groups) | 1,211 | 1,211 |
  | `unparseable` (all albers10k2m) | 2,245 | — |
  | `no_dsm_dir` | 2,900 | — |
  | `unpaired` | 173 | — |
  | **total** | **102,416** | |

- Two bugs the whole-bucket run surfaced, both now fixed and regression-tested:
  1. **Tile codes come in two widths.** `092h001212` (6 digits after the letter,
     1,659 tiles) was rejected by a `\d{3}[a-z]\d{3}` parser, which put 901 of the
     1,211 `.laz`-stranded tiles into `unparseable` instead of the coverage gap.
  2. **083d/2019 ships a re-issued DEM beside its original** (`..._2019.tif` and
     `..._2019_1.tif`) sharing one DSM. Both legitimately carry the asset, but the
     sharing is now reported — the identical shape would appear if the match key
     were too coarse, and those two cases must not look the same.
- The `unknown` convention count of 2 is that same 083d pair. The assertion design
  worked exactly as the issue intended: an unfamiliar name still paired on tile id
  and date, and surfaced in the report rather than dropping a DSM.
- Guards mutation-tested: collapsing `no_raster_dsm`, treating an empty listing as
  no-DSM, and dropping unpaired DEMs each fail the suite (1, 1 and 4 tests).

### Review findings folded in, verification passed — 2026-08-28

Concurrent code review returned six findings; all six were reproduced before
acting, and all six were real:

1. **A DSM-only month exited 0**, skipping the pairing step *and* the cache
   commit — the refreshed listings would have died with the runner. The exit
   contract now counts a DSM listing change as a change.
2. **No ratchet on the DSM listing.** DEM had a 90%-of-cached guard; DSM had only
   `== 0`. A DSM-heavy truncation passed both guards and would have stripped the
   asset off every item whose DSM fell out. Same ratchet added.
3. **utm zone padding.** The bucket spells it both ways — 26,042 `utm09` against
   8,049 `utm9` — and `092/092l/2023` carries a `utm09` DEM whose only DSM is
   spelled `utm9`. Confirmed reported as `unpaired`. Zone now normalised.
4. **`YYMMDD` dates were not recognised**, so `bc_082l024_2_3_3_..._170529` and
   `..._170607` — the same tile flown twice — collapsed to one match key. A
   bucket-wide sweep found exactly 40 DEM match-key collisions; 34 are the known
   parenthesized duplicates (#8), 2 are these flights, 2 the 083d re-issues.
5. **`sub("^.*/gdwuts/")` returns its input unchanged on no match**, so a URL
   shape change would silently emit full URLs as group names and flip every
   `.laz`-only tile to `no_dsm_dir`. Now asserts the derived shape.
6. **The conservation invariant was a bare `assert`**, stripped by `python -O`.
   Now an explicit raise.

Normalising utm exposed 121 tiles carrying *both* spellings as separate DSM
files. An arbitrary pick recorded them as `convention=unknown`, burying the 3
real unknowns in noise; the tie-break now prefers a DSM whose name relates to
the DEM's by a recognised convention. Final: 95,768 `suffix` (matching #29
exactly), 117 `identical`, 3 `unknown`.

**Sample verification passed** — 500 tiles stratified across convention and year:
COG-status agreement 0.9980 (threshold 0.99), footprint agreement 1.0000 over the
115 tiles whose DEM footprint is cached (threshold 1.00). The footprint rate is
computed over a smaller population on purpose: 60,324 of 100,345 cache rows
predate spatial-metadata caching and carry NaN, and scoring "never recorded" as
"differs" reported a false 15% agreement on the first run.

**A pre-existing bug surfaced by the end-to-end test**: cache lookups compared raw
URL strings, but `urls_list.txt` carries fs::path's single-slash `https:/` form
while every other source carries `https://`. A 6-URL build re-read all six over
the network and appended 6 duplicate rows to `stac_geotiff_checks.csv`. Both
sides now normalise; re-running the same build extracts 0 and leaves the cache
byte-identical.

End-to-end proof: 6 items built, both `image` and `dsm` assets present, 6/6 valid
under `item_validate.py`, collection carries providers/keywords with all 98,040
item links preserved.

### PR auto-review findings folded in — 2026-08-28

Two findings from the Claude Code review on PR #32, both real:

1. **I regressed the item-shortfall warning.** `Count created items` counts every
   JSON in `$STAC_OUTPUT_DIR`, and the new rebuild step writes into the same
   directory before it ran — so 20-of-100 new items failing could be masked by 30
   unrelated rebuilds and the warning would never fire. Now two separate counts:
   `Count new items` runs *before* the rebuild and owns the shortfall warning;
   `Count items to publish` runs after and gates validate/sync, since a
   pairing-only month produces item JSONs and no new URLs at all.
2. **`dsm_unparseable` was collected and never surfaced** — neither logged nor in
   the report, contradicting this PR's own "nothing is dropped silently" claim.
   Sharper second-order point: `dsm_groups` was built only from *parseable* DSM
   keys, so a mapsheet-year whose rasters all failed to parse would drop out and
   its DEM tiles would report `no_raster_dsm` — claiming point-cloud-only for a
   delivery that shipped rasters we merely could not name. The group is now
   derived from the path for unparseable keys too, and `unpaired` is the reported
   answer. Count is 0 against today's bucket, so this was latent, not wrong.

Both now have tests (35 total).

### Plan-agent review — three committed data regressions — 2026-08-28

The `plan-review-31` agent did deliver, late and by message. I had already told
the user it produced nothing; that was wrong, and its three blockers were real
data damage in commits already pushed. Full findings in `review-plan.md`.

**R1 — the validation ledger was wiped, 98,048 rows to 6.** My own end-to-end
smoke test (`item_validate.py --items-dir <scratch>`) replaced it, because a
non-incremental run rewrites the file wholesale. The second-order damage was
worse than the lost audit trail: `urls_reconcile.py` derives item-backed URLs from
this file, and `scripts/README.md` tells the operator to run `--apply` — which
would have truncated `urls_list.txt` from 102,416 to 6. Restored; a full run now
refuses to shrink an existing ledger without `--allow-shrink`, verified firing on
the destructive case and silent on the healthy one.

*One correction to the finding:* the leaked scratchpad paths are not mine. The
restored ledger already held 40,021 rows with `.../scratchpad/catchup_out/` paths
from the July catch-up. Pre-existing, worth its own issue, not a regression here.

**R2 — `urls_list.txt` advanced to 102,416 with none of the 4,420 built.**
`detect_changes.R` rewrites the cache as a side effect of detecting, so the next
run's setdiff is empty, `urls_new.txt` gets deleted, and those tiles are invisible
to change detection permanently — verbatim the failure `urls_reconcile.py` was
written to repair, with R1 having broken the repair path. Reverted to the 97,996
baseline; `urls_new.txt` keeps the 4,420 and the next run re-derives them.

**R3 — `urls_deleted.txt` deleted.** An append-only audit trail removed because
the current run found no deletions. It is #28's standing evidence, and the 43 lost
entries were all `albers10k2m_new/` — against 4,490 albers rows in
`stac_geotiff_checks.csv` for 2,245 live URLs, that is a prefix rename, which
supports #28's hypothesis over data loss. Restored; the script now appends and
never removes.

Also fixed: `item_reprocess.py` had no pairing (the documented remediation path
would silently strip the asset); the DSM side had no conservation check (added,
and it double-counted on the first attempt — unparseable keys were also in
`dsm_unmatched`); `FOOTPRINT_SAMPLE_MIN` tested the ~90k population rather than
the drawn sample, so it could never fire; four factual errors in `task_plan.md`,
including a match key omitting `utm` that would have put 91,008 of 95,751 DEMs
into a shared key.

Added the reviewer's suggested drift test: 157 R-derived groups, 146 parsed by
Python, difference exactly the 11 `.laz`-only ones.

**Process note.** I spawned this reviewer with a `name`, which made it a
persistent teammate that idles instead of delivering, and gave it no file-delivery
instruction. It then had no way to write its findings — it was read-only. Three
idle pings looked identical to "nothing to say", and I reported it as such before
the content arrived. Reviewers should be spawned unnamed, with the delivery path
in the first prompt, and an idle ping must never be read as a clean review.

### Release attempt 1 failed on my own exit code — 2026-08-29

CI run 33268573094, dispatched with `backfill=true`. Everything upstream passed,
including the 4,420 building with **no remote extraction** because the committed
metadata cache paid off. The backfill then completed all 98,040 items in 16m37s at
~98 it/s — faster than the laptop — and the deepdiff verify passed:

```
written 91556 | unchanged 6482 | error 2      (sums to 98,040)
Verify passed: the only differences are the intended ones
##[error]Process completed with exit code 1
```

**The work succeeded; `return 1 if counts["error"] else 0` failed it.** A strict
zero-error gate on ~98k network requests. 2 failures — 0.002% — discarded 16m37s
of verified work and skipped the publish.

This is the mirror of the convention already in CLAUDE.md ("a guard must not fail
toward skip"): a guard must not fail toward **abort** on an operation where
partial failure is certain. I predicted this exact risk in writing before
dispatching and shipped it anyway.

Three consequences, and the second was the expensive one:

1. Sync skipped — 91,556 correct items discarded.
2. **The manifest was never persisted.** `data/backfill_done.txt` only commits via
   "Commit refreshed caches", which was skipped on failure, so a re-run would have
   restarted from zero rather than resuming 98,038 successes.
3. The 2 error messages never reached the log at all — tqdm writes its progress
   bar to stderr with carriage returns and overwrote the interleaved warnings.
   Second time today the same interleaving hid diagnostics from me.

Fixes, all tested:

- **Retry in-process**, 3 attempts with linear backoff. Locally every transient
  failure re-fetched fine on the next try, so this absorbs the ordinary case
  before it can reach the exit code.
- **Gate on error RATE** against a stated tolerance (0.1%, 200 absolute) rather
  than on perfection. Tested against both known answers: 2/98,040 and 34/98,040
  are accepted; 5%, 500 absolute, and 3/100 are refused.
- **Per-item failures to `data/backfill_errors.txt`**, not stderr, so tqdm cannot
  clobber them and the failing ids are always recoverable.
- **`if: always()` on the cache commit**, so a failed run still persists what it
  completed.

Also noted: PWF had gone stale — four commits with no update, and `task_plan.md`
had zero mention of the backfill or the release. Phase 9 added retroactively.
The convention exists precisely so the long tail of a task stays legible, and I
dropped it exactly when the task got long.

### v1.0.0 released — 2026-08-29

CI run 33270745597 (`backfill=true`) published cleanly after the error-tolerance
fix: `written 91558 | unchanged 6482 | error 0` — the retry took the 2 transient
failures to zero. `4420 of 4420` new items, `95,978 valid (100.0%)` through the
non-vacuous gate, `Sync complete: 95978 item(s) + collection.json`.

Verified against live S3 and the API, not inferred from a green run:

| | before | after |
|---|---|---|
| items on S3 | 98,040 | 102,460 |
| items in pgstac / API | 60,126 | 102,460 |
| items with `dsm` | 0 | 95,888 |
| raw-space (unusable) hrefs | 90 | 0 |
| `providers` / `keywords` | absent | live |

A formerly-broken asset href now returns **206** where it previously returned
`000` — the request could not be formed at all. Those 90 items had an unusable
download link from the day they were built.

**pgstac registration caused a real outage.** rtj's `stac_register-pypgstac.sh`
DELETEs the collection (step 2) before loading it (step 5). It died in between on
`cat "$FETCH_DIR"/*.json` — ARG_MAX, ~6 MB of argv at 102,460 filenames — leaving
`images.a11s.one` serving **zero items** until repaired by hand. The downloads had
all succeeded; only the concatenation failed. `find -exec cat {} +` produced all
102,460 lines and pypgstac loaded them in **27 seconds**.

That was a *recurrence*: rtj#196 hit the identical failure in 2026-07, wrote the
ARG_MAX entry into soul's `code-check.md`, and never repaired the script — while
the convention's *other* entry still prescribed the broken glob. Both fixed
(`soul@95eddb2`, rtj PR #238).

README rebuild: `README.md` had been stale since 2026-08-05. The refreshed rstac
query returns 64 features with no `dsm`; verified as correct rather than assumed —
all 64 are in `093/093m/2020`, which the pairing records as `no_dsm_dir`.

Spun out: #34 (rename to stac-elevation-bc, carrying the two breaks #31 deferred),
#35 (point cloud 175k / orthophoto 15.7k / CHM 264 — none indexed, and point cloud
is what would actually close the 1,211-tile `no_raster_dsm` gap).

Three bug classes appended to `soul/conventions/code-check.md` (`497b20c`):
fail-toward-abort, progress-bars-eat-log-lines, and fix-the-writer-reconcile-the-data.
