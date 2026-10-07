# Review: #51 Phase 3 (pin ngr v0.0.3 + migrate caches), round 1

## Clean

No bugs, security issues or data-loss paths found in the staged commit.

## What was checked, and how

**Migration reproduces exactly.** In a copy (`scratchpad/rv3`), the four HEAD files were
restored and `scripts/urls_scheme_migrate.py` was run on them. All four outputs are
byte-identical (`cmp`) to the staged blobs. A second run against the migrated data
reports `0 rewritten` and an unchanged line count for every file.

**txt files.** For `urls_list.txt` (102,416), `urls_dsm.txt` (95,889) and
`urls_deleted.txt` (43), `sed 's#^https:/\([^/]\)#https://\1#'` over HEAD gives the
staged file exactly. There are no CRLFs (`grep -c $'\r'` = 0), every file ends in `\n`,
there are no duplicate lines before or after, and no line in any of them lacks `https://`.

**stac_geotiff_checks.csv.** Parsed with `csv.reader`: old has 104,766 records and
104,766 lines, new has 102,461 and 102,461, so there are no embedded newlines. Every
record has 8 columns. No line starts with `"` and no URL contains a comma, so the
script's `split(",", 1)[0]` URL extraction is exact for this file. No other column holds
a URL. New has 102,460 unique URLs, which equals the old set once its spelling is
normalised. The script asserts that every dropped row is byte-identical to the kept one.
`is_cog=True` went from 4,469 to 4,409, consistent with dropped duplicates only.

**Readers of the CSV.** None of them depend on duplicate rows, row order or row count:
- `item_create.load_validation_cache` builds sets and dicts keyed on `fix_url(url)`. Its
  `needs_upgrade` drop uses `.map(fix_url).isin(...)`, which removed both spellings
  before and removes the single row now. The concat uses `ignore_index=True`, with no
  positional join.
- `item_reprocess.py:171` and `dsm_verify.py:152` use dict lookups on `fix_url(url)`.
  With duplicates byte-identical, first-wins and last-wins gave the same entry.
- No test, doc or script asserts 104,765, 104,766 or 4,490 as a current count.
  `detect_changes.R:124` mentions 4,490 in the past tense, which is accurate.

**Nothing in the update.yml path still produces or expects `https:/`.**
- `detect_changes.R` checks all three caches with `urls_scheme_assert` before the walk,
  and `urls_listing_fetch` checks the walk. With ngr 0.0.3 and the migrated caches, both
  sides are `https://`.
- `dsm_pair.py:479` writes `f"{PATH_S3}/{k}"`, which is `https://`.
- `item_create` appends rows using the raw `urls_new.txt` line, now `https://`.
- `urls_check_access` and `register_manifest` use `fix_url`, which is idempotent.
- `url_to_item_id` (`stac_utils.py:433`) gives the same id for either spelling, because
  `lstrip("/")` absorbs the one-character offset.
- `stac_create_item.qmd:231`'s non-idempotent `.replace("https:/", "https://")` now
  turns every cached URL into `https:///…`, so the qmd would skip every tile as
  unreadable. **This does not affect update.yml.** The workflow runs no quarto or qmd
  step (grep of `.github/` for `qmd` and `quarto` returns nothing). It stays scheduled for
  Phase 4 as planned.

**DESCRIPTION.** Tag `v0.0.3` exists on NewGraphEnvironment/ngr
(`25d3911`), and its DESCRIPTION reads `Version: 0.0.3`. So `ngr (>= 0.0.3)` is
satisfiable by `Remotes: NewGraphEnvironment/ngr@v0.0.3`, and the `user/repo@tag` ref
syntax is valid for pak. `environment.yml` does not pin ngr, so there is no second pin
to conflict with.

**Data-invariant test** (`test_no_tracked_data_file_holds_a_one_slash_url`):
- It scans 15 text files under `data/`, against a floor of 4.
- `stac_result.rds` is gzip-compressed and correctly skipped: there is a NUL in its
  first 8 KiB. Its decompressed contents hold 0 one-slash URLs anyway.
- Restoring the bug: putting HEAD's `urls_deleted.txt` back in the copy makes the test
  fail, and the staged file makes it pass.
- `git ls-files` runs with `check=True` and a missing file raises, so it cannot pass
  vacuously in the ways that matter here.

**Full suite** in the copy: `207 passed`.

**origin/main** has not moved past the merge base (`f88c6ab`). The 2026-10-03 scheduled
run committed no data, so the migrated caches are not stale relative to main.

## Non-blocking notes (not findings)

- The migration script's set-equality asserts derive both sides through `scheme_fix`,
  so they are close to tautological and could not fail on a scheme_fix defect. The
  independent checks were the live ngr 0.0.3 walk and this review's `sed` and `cmp`
  reproduction, and those carry the weight. The script also relies on `assert`, which
  `python -O` strips. Neither matters for a one-shot that has already been applied and
  is a no-op on re-run.

## Statements that now describe the old storage form (for Phase 4, not bugs)

- `CLAUDE.md:146-153`: "Source URLs are stored as `https:/`", "`stac_geotiff_checks.csv`
  holds both", and "any reader … goes through `fix_url` first"
- `scripts/item_create.py:211-215`: comment saying `urls_list.txt` carries the
  single-slash form
- `scripts/stac_utils.py:189-190`: comment, same claim
- `scripts/register_manifest.py:32-33`: docstring, "source lists … store the scheme as
  `https:/`"
- `tests/test_register_manifest.py:48`: docstring, "`data/urls_list.txt` carries
  `https:/` … on every line"
- `tests/test_dsm_pair.py:232` and `:404`: docstrings, same claim
- `stac_create_item.qmd:231`: the non-idempotent replace (see above)
- `NEWS.md:55-56` describes the old form in a past release entry. It is historical and
  accurate as history.
