# CLAUDE.md - STAC DEM BC Project Guidelines

## Project Overview: Automated Monthly STAC DEM BC Updates

This project maintains the STAC catalog for BC's LidarBC DEM collection with automated monthly updates: a GitHub Actions workflow (`update.yml` — cron + workflow_dispatch, OIDC to S3) runs change detection and an incremental build, then commits refreshed caches back to main. Performance patterns (parallel processing, pre-validation) were ported from stac_orthophoto_bc.

**Architecture:** GitHub Actions cron → Change detection → Parallel validation/processing → S3 sync → PgSTAC registration (manual step on geoserv; incremental upsert tracked in the infrastructure repo)

**Expected Performance:**
- First run (full): ~1-1.5 hours (down from 5-6 hours)
- Monthly runs (incremental): scale with volume — a typical ~8k-file month is ~75–90 min on the runner; a no-change month exits in ~6 min
- Cost: $0 additional (uses existing VM)

### Key Implementation Phases

**Phase 1-2: Modernization ✅ COMPLETE (2026-02)**
- Port stac_orthophoto_bc performance improvements
- Pre-validation system with COG detection
- Parallel item creation using ThreadPoolExecutor
- Incremental update logic with change detection
- Optimize spatial extent calculation
- **Result:** 100-item test passed, ready for VM automation

**Phase 3: Automation ✅ COMPLETE (2026-07, #23)**
- Landed as the monthly GitHub Actions workflow (`.github/workflows/update.yml`), not the originally-planned VM cron (that VM was never built)
- First scheduled run 2026-08-03 handled a deletions-only month correctly
- Remaining follow-ups: incremental pgstac registration (infrastructure repo), upstream-deletion pruning (#28)

### Project Context

**Dataset:** ~98,000 DEM GeoTIFFs from BC provincial objectstore (nrs.objectstore.gov.bc.ca/gdwuts), as of 2026-08
- History of large undocumented growth: 22,548 → 58,109 (discovered Feb 2026), then +63% to 98,039 in five months (July 2026 catch-up, #23) — arrival may be bulk loads, not steady monthly
- ~90 files with parentheses in filename excluded (all fail validation - see issue #8)

**Actual Performance (Feb 2026 - Full Build):**
- 58,028 items created in ~5.5 hours (~6,450 items/hour)
- Validation caching working (cache fix applied)
- Parallel processing with 32 workers
- 99.86% success rate (81 items failed/missing)
- **Bottleneck:** Network I/O reading remote GeoTIFFs for metadata

**Current Status:**
- ✅ Incremental update capability (change detection working)
- ✅ Validation caching (GeoTIFF validation)
- ✅ STAC JSON validation layer (new)
- ✅ Monthly automation via GitHub Actions (`update.yml`: cron 3rd of month + workflow_dispatch, OIDC to S3; pgstac registration remains a manual geoserv step)
- ✅ Spatial extent optimized (hardcoded BC bbox)

**Goals:**
1. ~~Reduce full processing time to ~1-1.5 hours~~ → **Reality: 5-6 hours** (network I/O limited)
2. ✅ Monthly incremental updates via GitHub Actions (typical month fits the runner comfortably; oversized batches fall back to a local run)
3. ✅ Implement robust validation and error handling
4. ✅ Automated monthly updates — GitHub Actions, not VM cron (#23; catalog 98k items as of 2026-07)
5. ✅ Maintain audit trail and benchmarking

**Key Learning:** Performance is network I/O bound, not CPU bound. Future optimization: local metadata caching (Issue #10).

### Related Work
- **stac_orthophoto_bc:** Reference implementation for parallel processing patterns
- **stac_uav_bc:** VM deployment patterns and automation functions
- **Issue #3:** Proper GeoTIFF validation and media type assignment

### Data Tracking & Validation System

**File-based tracking for quality assurance and incremental updates:**

```
data/
├── urls_list.txt              # Master URL list from BC objectstore (~98k URLs)
├── urls_new.txt               # New URLs detected by change detection
├── urls_deleted.txt           # Deleted URLs (audit trail)
├── stac_geotiff_checks.csv    # Source validation (url, is_geotiff, is_cog)
└── stac_item_validation.csv   # Output validation (item_id, json_valid, error)
```

**Validation layers:**
1. **GeoTIFF validation** (`stac_geotiff_checks.csv`) - Validates source data quality
   - Checks if URL is readable GeoTIFF
   - Detects Cloud-Optimized GeoTIFF status
   - Caches results to avoid re-validation
   - Used during item creation to skip invalid sources

2. **STAC JSON validation** (`stac_item_validation.csv`) - Validates output data quality
   - Checks generated STAC item JSONs are valid
   - Uses pystac for spec compliance
   - Tracks validation errors for debugging
   - Filters items before PgSTAC registration
   - Script: `scripts/item_validate.py`

**Workflow integration:**
```
Source URLs → GeoTIFF Validation → Item Creation → JSON Validation → Registration
 (urls_list)   (geotiff_checks)      (.qmd/.py)    (item_validation)   (pgstac)
```

**Key insight:** Separation of source quality (can we read it?) from output quality (is STAC valid?) enables better debugging and incremental processing.

### Script Evolution: .qmd → .py

**Current state:**
- `.qmd` files: Good for exploration, mixed R/Python workflows
- `.py` scripts: Better for production, automation, testing

**Migration strategy (Issue #7):**
- New scripts: Write as pure Python (`.py`)
- Existing `.qmd`: Migrate gradually to standalone scripts
- Keep `.qmd`: For documentation/examples if useful

**Benefits of .py for production:**
- Better IDE support and debugging
- Easier testing and CI/CD integration
- Cleaner for cron/automation
- Standard Python packaging and distribution
- No R dependency for core workflows

---

## Project-Specific Notes

### Testing Strategy
- Use `test_only = True` and `test_number_items = 10` for development
- Test in worktrees before merging to main
- Validate with dev S3 bucket and PgSTAC instance
- Benchmark timing at each phase
- Verify STAC API queries through images.a11s.one

**IMPORTANT: Always run tests and production with logging enabled:**
```bash
# Test run with logging
quarto render stac_create_item.qmd --execute 2>&1 | tee logs/$(date +%Y%m%d_%H%M%S)_test_phase1_10items.log

# Production run with logging
quarto render stac_create_item.qmd --execute 2>&1 | tee logs/$(date +%Y%m%d_%H%M%S)_prod_full_run.log
```
Logs capture: configuration, validation progress, item creation, errors, warnings, timing, and summary statistics.

### Key Trade-offs Documented in Issues
- **Spatial extent:** Hardcoded BC bbox vs calculated (saves ~20 minutes, BC boundary stable)
- **Validation caching:** Pre-validate all files vs validate on-demand (frontload cost, faster iterations)
- **Parallel processing:** ThreadPoolExecutor vs multiprocessing (avoid rasterio threading issues)

### Parallel Processing & Performance Patterns

**Proven from Phase 1-2 (stac_orthophoto_bc + stac_dem_bc):**

**1. ThreadPoolExecutor for Rasterio Operations**
```python
# CORRECT: Works reliably with rasterio
with concurrent.futures.ThreadPoolExecutor() as executor:
    results = list(executor.map(process_geotiff, urls))

# WRONG: Causes threading conflicts, hangs/crashes
with multiprocessing.Pool() as pool:
    results = pool.map(process_geotiff, urls)
```
WHY: Rasterio uses internal threading that conflicts with multiprocessing. ThreadPoolExecutor avoids these conflicts while still providing parallelism for I/O-bound operations (reading remote GeoTIFFs via /vsicurl/).

**2. Validation Caching Strategy**
- Pre-validate all files in parallel using `rio cogeo validate`
- Cache results in CSV (`url, is_geotiff, is_cog`)
- Skip unreadable files during item creation (logged, not fatal)
- Incremental mode: only validate new URLs not in cache
- **Benefit:** Frontload ~20-30 min cost once, skip 100-500 invalid files on every subsequent run

**3. Test Mode Design Pattern**
When implementing test modes that support both clean runs and incremental appends:
```python
if test_only and not incremental:
    # Clear BOTH metadata AND files
    collection.links = [link for link in collection.links if link.rel != 'item']
    for old_json in glob.glob(f"{path_local}/*-*.json"):
        os.remove(old_json)
```
WHY: Clearing only collection links leaves orphaned JSON files across test runs. Must clean both to prevent accumulation and mismatches.

**4. Incremental Mode Duplicate Prevention**
```python
existing_item_hrefs = {link.target for link in collection.links if link.rel == 'item'}
for result in results:
    item_href = f"{path_s3_stac}/{result['id']}.json"
    if item_href not in existing_item_hrefs:
        collection.add_link(Link(...))
```
WHY: Reprocessing same URLs (e.g., after failures, testing) would create duplicate links without explicit checking. PySTAC doesn't prevent duplicates automatically.

**5. Dataset Monitoring**
- BC DEM objectstore grew 158% undocumented (22,548 → 58,109 files)
- Change detection discovered 35,569 new files, 8 deleted
- **Lesson:** Always implement monitoring/change detection for external data sources, even if "stable"

### Dependencies
- Python: pystac, rio_stac, rasterio, rio-cogeo, pandas, tqdm, concurrent.futures (built-in)
- System: rio CLI tools (rasterio[cogeo])
- Infrastructure: DigitalOcean VM (stac-prod), S3 (stac-dem-bc), PgSTAC

### Infrastructure Management

**Current State (Phase 1-3):**
- VM deployment: Manual via `vm_upload_run()` function from stac_uav_bc
- S3 management: AWS CLI commands
- Server provisioning: Scripts similar to stac_uav_bc setup

**Future Migration (Post-Phase 3):**
- **rtj repository:** `/Users/airvine/Projects/repo/rtj` (formerly awshak)
- OpenTofu/Terraform-based infrastructure management
- S3 buckets already IaC-managed: `stac-dem-bc` (prod), can easily create `dev-stac-dem-bc` for testing
- Other managed buckets: imagery-uav-bc, stac-orthophoto-bc, water-temp-bc, backup-imagery-uav
- Features: versioning, lifecycle policies, CORS, public access controls
- Reproducible, version-controlled server setups (future)

**Note:** The monthly update runs on GitHub-hosted runners (no VM). S3 buckets and the OIDC role are IaC-managed in rtj; the pgstac host (geoserv) is rtj-provisioned.

### File Locations
- **Main repo:** `/Users/airvine/Projects/repo/stac_dem_bc`
- **Phase 1-2 worktree:** `/Users/airvine/Projects/repo/stac_dem_bc-phase1-2-modernization`
- **Infrastructure repo:** `/Users/airvine/Projects/repo/rtj` (formerly awshak; provisions the bucket, OIDC role, and geoserv STAC host)
- **STAC catalog:** `s3://stac-dem-bc/` is the only complete copy. Local builds write to a scratch workspace via the `STAC_OUTPUT_DIR` env override in `scripts/stac_utils.py` (the old `/Users/airvine/Projects/gis/.../stac/prod` dir is empty/historical)

<\!-- BEGIN SOUL CONVENTIONS — DO NOT EDIT BELOW THIS LINE -->


# Cartography

## Style Registry

Use the `gq` package for all shared layer symbology. Never hardcode hex color values when a registry style exists.

```r
library(gq)
reg <- gq_reg_main()  # load once per script — 51+ layers
```

**Core pattern:** `reg$layers$lake`, `reg$layers$road`, `reg$layers$bec_zone`, etc.

### Translators

| Target | Simple layer | Classified layer |
|--------|-------------|-----------------|
| tmap | `gq_tmap_style(layer)` → `do.call(tm_polygons, ...)` | `gq_tmap_classes(layer)` → field, values, labels |
| mapgl | `gq_mapgl_style(layer)` → paint properties | `gq_mapgl_classes(layer)` → match expression |

### Custom styles

For project-specific layers not in the main registry, use a hand-curated CSV and merge:

```r
reg <- gq_reg_merge(gq_reg_main(), gq_reg_read_csv("path/to/custom.csv"))
```

Install: `pak::pak("NewGraphEnvironment/gq")`

## Map Targets

| Output | Tool | When |
|--------|------|------|
| PDF / print figures | `tmap` v4 | Bookdown PDF, static reports |
| Interactive HTML | `mapgl` (MapLibre GL) | Bookdown gitbook, memos, web pages |
| QGIS project | Native QML | Field work, Mergin Maps |

## Key Rules

- **`sf_use_s2(FALSE)`** at top of every mapping script
- **Compute area BEFORE simplify** in SQL
- **No map title** — title belongs in the report caption
- **Legend over least-important terrain** — swap legend and logo sides when it reduces AOI occlusion. No fixed convention for which side.
- **Four-corner rule** — legend, logo, scale bar, keymap each get their own corner. Never stack two in the same quadrant.
- **Bbox must match canvas aspect ratio** — compute the ratio from geographic extents and page dimensions. Mismatch causes white space bands.
- **Consistent element-to-frame spacing** — all inset elements should have visually equal margins from the frame edge
- **Map fills to frame** — basemap extends edge-to-edge, no dead bands. Use near-zero `inner.margins` and `outer.margins`.
- **Suppress auto-legends** — build manual ones from registry values
- **ALL CAPS labels appear larger** — use title case for legend labels (gq `gq_tmap_classes()` handles this automatically via `to_title()` fallback)

## Self-Review (after every render)

Read the PNG and check before showing anyone:

1. Correct polygon/study area shown? (verify source data, not just the bbox)
2. Map fills the page? (no white/black bands)
3. Keymap inside frame with spacing from edge?
4. No element overlap? (each in its own corner)
5. Legend over least-important terrain?
6. Consistent spacing across all elements?
7. Scale bar breaks appropriate for extent?

See the `cartography` skill for full reference: basemap blending, BC spatial data queries, label hierarchy, mapgl gotchas, and worked examples.

## Land Cover Change

Use [drift](https://github.com/NewGraphEnvironment/drift) and [flooded](https://github.com/NewGraphEnvironment/flooded) together for riparian land cover change analysis. flooded delineates floodplain extents from DEMs and stream networks; drift tracks what's changing inside them over time.

**Pipeline:**

```r
# 1. Delineate floodplain AOI (flooded)
valleys <- flooded::fl_valley_confine(dem, streams)

# 2. Fetch, classify, summarize (drift)
rasters   <- drift::dft_stac_fetch(aoi, source = "io-lulc", years = c(2017, 2020, 2023))
classified <- drift::dft_rast_classify(rasters, source = "io-lulc")
summary    <- drift::dft_rast_summarize(classified, unit = "ha")

# 3. Interactive map with layer toggle
drift::dft_map_interactive(classified, aoi = aoi)
```

- Class colors come from drift's shipped class tables (IO LULC, ESA WorldCover)
- For production COGs on S3, `dft_map_interactive()` serves tiles via titiler — set `options(drift.titiler_url = "...")`
- See the [drift vignette](https://www.newgraphenvironment.com/drift/articles/neexdzii-kwa.html) for a worked example (Neexdzii Kwa floodplain, 2017-2023)


# CI Monitoring

When this repo has GitHub Actions workflows, scan recent runs on session start. Catches failed pkgdown deploys, broken vignette builds, and stale citation regenerations that would otherwise linger until the user manually checks.

## On Session Start

```bash
gh run list --limit 5 --json status,conclusion,name,createdAt,databaseId \
  --jq '.[] | select(.conclusion == "failure")'
```

If any failures since the last visit, surface to the user before starting other work:

> Workflow `<name>` failed `<time>` ago (run `<id>`). Investigate with `gh run view <id> --log-failed`. Fix or proceed with current task?

User decides; do not auto-fix.

## Particular Failures Worth Naming

- **pkgdown** — docs site on GitHub Pages broken
- **R-CMD-check** — package may not install
- **Vignette / build-vignettes** — vignette docs incomplete
- **update-citation-cff** — CITATION.cff stale

## Why This Matters

Without this scan, post-merge workflow failures linger until someone (often the user) notices a stale docs site or a missing vignette. The session-start sweep catches them on the first re-entry into the repo.

## Pairs with `/gh-pr-merge`

The skill watches workflows triggered by a fresh merge in real time — that's the targeted catch. This convention is the backstop for failures that landed when no one was watching (merges via web UI, scheduled triggers, manually-triggered workflows).


# Code Check Conventions

Structured checklist for reviewing diffs before commit. Used by `/code-check`.
Add new checks here when a bug class is discovered — they compound over time.

## Shell Scripts

### A guard must not fail toward "skip"
- When a check decides whether to do something consequential (cut a tag, send a
  mail, run a migration), work out which way it fails when the command inside it
  errors. If the error path and the "nothing to do" path look the same, the
  guard is indistinguishable from a working one right up until it silently eats
  the action.
- `IF=$(some-cmd ...)` inside `[ -z "$IF" ]` is the usual shape: the command
  aborts, stdout is empty, and empty reads as "nothing changed". **Assign first,
  test the exit status, then test the value.**
  ```bash
  if OUT=$(git diff --name-only "$A".."$B" -- . "${EXCL[@]}" 2>/dev/null); then
    [ -z "$OUT" ] && NOTHING_CHANGED=1     # only trust emptiness on success
  fi
  ```
- Caught 2026-08-12 in soul's `gh-pr-merge` release gate: the diff aborted, the
  empty output read as "nothing shipped", and a branch of five commits of real
  package changes was classified as needing no release.
- **Test a guard against both known answers before shipping it.** One case that
  should fire and one that should not. The draft above returned the same value
  for both, which reading the code did not reveal.

### git pathspec excludes: use the long form
- `:!path` is short-form magic, and git keeps parsing magic characters after the
  `!`. A path starting with one aborts the whole command:
  `:!_pkgdown.yml` → `fatal: Unimplemented pathspec magic '_'`.
- Use `:(exclude)path`. `:!./path` also works, but the long form says what it means.
- Anything building pathspecs from a file (`.Rbuildignore`, `.gitignore`) will
  eventually meet a leading `_`, `(`, or `^`.

### Reading a file line-by-line drops the last line without a trailing newline
- `while IFS= read -r line; do ...; done < file` skips a final line that has no
  newline after it. Use `while IFS= read -r line || [ -n "$line" ]`.

### Empty arrays under `set -u` on bash 3.2
- macOS still ships bash **3.2**, where `"${ARR[@]}"` on an empty array is an
  unbound-variable error under `set -u`. Guard with `[ ${#ARR[@]} -gt 0 ]`
  before expanding. Scripts written and tested on Linux bash 5 hit this only on
  a Mac, and only when the array happens to be empty.

### Quoting
- Variables in double-quoted strings containing single quotes break if value has `'`
- `"echo '${VAR}'"` — if VAR contains `'`, shell syntax breaks
- Use `printf '%s\n' "$VAR" | command` to pipe values safely
- Heredocs: unquoted `<<EOF` expands variables locally, `<<'EOF'` does not — know which you need
- Unquoted heredocs also run **command substitution**: backticks in prose (markdown code spans!) execute and are replaced by their output, usually empty. Writing markdown through an unquoted heredoc silently deletes every `` `word` `` in it — no error, and the damage only shows on re-read. Seen 2026-08-06 writing a memory index line: a markdown code span followed by "gone as a concept" landed as "gone as a concept", subject removed. Any heredoc carrying prose or markdown wants `<<'EOF'`.
- Pass-through-ssh args: `printf '%q'` escapes per-arg so workload paths with spaces / quotes / metacharacters survive the local-shell → ssh-argv → remote-shell round-trip. Without it, `ssh host 'cmd' "$path"` joins args with spaces on remote and re-parses, losing argument boundaries.
- `git commit -m "$(cat <<'EOF' ... EOF)"` chokes on apostrophes in prose bodies in some contexts — the bash parser surfaces an unmatched-quote error even though heredoc bodies should be quote-neutral. Resilient default for multi-line commit messages: write the body to `/tmp/msg.txt` and use `git commit -F /tmp/msg.txt`.
- **The same trap has a silent variant: `Rscript -e` / `python -c` carrying backslash escapes.** The heredoc case above fails loudly, which costs a retry. Passing a regex inline does not: `\\b` reaches the interpreter mangled, so `grepl()` returns 0 matches against text it matches perfectly from a file. Nothing errors. Seen 2026-07-31 in rfp#93 — the 0 read as "my regex is wrong" and nearly triggered a rewrite of working code; the identical regex scored 4 matches the moment it ran from `/tmp/x.R`.
  - Rule: anything carrying a regex, nested quotes or backslashes gets written to a file and run (`Rscript /tmp/x.R`). Inline `-e` is for trivial one-liners only.
  - Diagnostic: when an inline command returns a surprising *result* rather than an error, suspect the quoting layer before the code, and re-run from a file to find out which is wrong. That one step separates a real bug from a shell artifact.

### Merging stderr into stdout corrupts the stdout you are parsing
- `system2(cmd, stdout = TRUE, stderr = TRUE)` (and `2>&1` generally) interleaves
  the two streams **without respecting line boundaries**, so a write on stderr
  can land in the middle of a stdout line. If you are parsing that line, it fails
  — not with a missing value, but with trailing garbage:
  ```
  RFPVALUEMAPS {...,"chain":["finder","surveyor's chain"]}QObject::killTimer: Ti
                                                        ^ parse error here
  ```
- **It only shows up on a long line**, which is what makes it a latent trap: the
  probe worked for a year against a 20-field payload and broke the first time it
  met a 145-field one. Nothing about the change looks related.
- Fix: send stderr to a **file**, keep stdout clean, and read the file back only
  when reporting a failure — so diagnostics are not lost:
  ```r
  err <- tempfile(); on.exit(unlink(err), add = TRUE)
  out <- system2(cmd, args, stdout = TRUE, stderr = err)
  # ... on failure: paste(utils::tail(readLines(err, warn = FALSE), 30), collapse = "\n")
  ```
- Anything chatty on stderr does this — Qt, GDAL, JVM warnings, progress bars.
  Suspect it whenever a subprocess parser fails on *content* rather than on
  absence.

### Heredoc precedence in pipelines
- `cmd1 | cmd2 <<EOF` — the heredoc binds to `cmd2` (the rightmost simple command). If you intended `cmd1` to receive it, put `<<EOF` on cmd1 explicitly: `cmd1 <<EOF | cmd2`.
- Symptom when wrong: ssh body silently echoed by tee/cat/etc, ssh side gets empty stdin, exits 0 (or near-0) without doing anything. Caught the hard way 2026-05-01 in cypher_restore-fwapg.sh.

### pipefail with ssh+tee
- `set -eu` does NOT propagate exit codes through pipelines. `ssh ... | tee log` returns tee's exit (always 0 for healthy tee), masking ssh failure.
- Use `set -euo pipefail` for any script that pipes a meaningful command into tee/cat/grep/etc. Or check `${PIPESTATUS[0]}` explicitly.
- Symptom when wrong: task notifications report "exit 0 / completed" while remote work was actually skipped or errored.

### A wrapper's exit 0 is not "the work completed" — gate on in-band error + output mtime
- A wrapper reports its OWN exit, not the inner job's. `caffeinate -s bash -c '...'`, `/usr/bin/time -p …`, and background tasks routinely surface **exit 0 / "completed"** while the wrapped R/Python script hit `Execution halted` partway. The interpreter's error goes to the log, not the wrapper's exit code.
- **Most dangerous in A/B validation:** if the run crashes *before* it (re)writes its output file, a compare step reads the **stale previous output** and reports a false "identical / passed" — a false positive that looks like success.
- Before trusting any run's result, gate on BOTH:
  1. **In-band error markers** — `grep -c "Execution halted\|Error:" "$log"` is 0 (R); the language's equivalent otherwise.
  2. **The output was actually (re)written** — its mtime is newer than a marker touched at run start (`[ output -nt "$marker" ]`), not merely that the file exists.
- Caught the hard way 2026-07 in `floodplains`: a Pass-2 reuse change was declared "12.4×, byte-identical" and **merged to main** — but the run had `Execution halted` before writing, so the A/B compared the unchanged baseline against its own backup. Broke every step-3 run until hotfixed. Same class as the ssh+tee pipefail symptom above, generalized to any wrapped/background job.

### Paths
- Hardcoded absolute paths (`/Users/airvine/...`) break for other users
- Use `REPO_ROOT="$(cd "$(dirname "$0")/<relative>" && pwd)"`
- After moving scripts, verify `../` depth still resolves correctly
- Usage comments should match actual script location

### Diagnose env/PATH problems in the shell that actually runs, not the ambient one
- Get ground truth **before** forming any theory:
  `env -i HOME=$HOME TERM=$TERM bash -lc 'echo $PATH | tr ":" "\n" | nl'`
  (swap in `zsh` to check the other side). Numbering shows ordering and
  duplication in one read.
- **Claude Code runs bash regardless of the user's login shell**, so a PATH
  measured from an agent shell says nothing about the terminal the user sees.
  Establish which shell is interactive (`echo $0`, or the prompt style) before
  opening any rc file.
- **The mutation is usually one level down from the obvious file.** A
  `for file in ~/.{path,exports,aliases,extra}; do source "$file"; done` loop in
  `.bash_profile` hides real `PATH=` assignments in files you never opened. Grep
  every sourced file, not just the rc files.
- Caught 2026-08-19: a 39-entry PATH with 12 duplicates took **three** wrong
  diagnoses — `.zprofile` (which did run `brew shellenv` five times, but the
  interactive shell was bash, so it was irrelevant), then `.bashrc` sourcing
  `.bash_profile`, then tmux inheriting a stale env. The cause was `~/.path`
  hand-prepending what `brew shellenv` already sets, plus three directories that
  no longer existed. One `env -i` run ended it.
- The same mistake closed an infra issue prematurely: MacPorts was removed and
  verified **in bash**, while `.zprofile` kept exporting `/opt/local/bin` on
  every zsh login for months. Verified in one shell, broken in the one that runs.

### Silent Failures
- `|| true` hides real errors — is the failure actually safe to ignore?
- Empty variable before destructive operation (rm, destroy) — add guard: `[ -n "$VAR" ] || exit 1`
- `grep` returning empty silently — downstream commands get empty input

### `cmd > file` truncates before `cmd` runs — a failed command leaves a poisoned empty file
- The shell creates/truncates the redirect target **before** the command executes. If the command then fails (times out, wrong arg, no network), you're left with a **zero-byte file** — not the absence of a file. `set -euo pipefail` does not save you: the truncation already happened before the command's non-zero exit fires.
- The trap springs on the *next* run when an **existence-only guard** treats that empty file as valid: `[ -f "$f" ] || cmd > "$f"` sees the file, skips regeneration forever, and every downstream reader silently consumes an empty value. For a secret/credential cache this reads as a confusing auth failure (empty header → `403`) with no obvious cause.
- Caught 2026-08 in cyclops#10: `op read "op://..." > ~/.config/newgraph/zotero-api-key` guarded by `[ -f ]` — a timed-out 1Password approval would have written an empty file that the guard then blessed permanently.
- Fix — three parts:
  1. Guard on **non-empty**, not existence: `[ -s "$f" ]`.
  2. Write **atomically** so a partial/failed run lands nothing: `cmd --out-file "$tmp" && chmod … && mv "$tmp" "$f"` (or `cmd > "$tmp" && mv`), with `trap 'rm -f "$tmp"' EXIT`.
  3. Prefer a tool's own `--out-file`/`-o` over `>` where it exists — the value never transits stdout, so `set -x`/`tee`/a pipeline can't capture it.

### Empty is not unset — `VAR=` passes a presence check that `unset` fails
- A command-scoped assignment built from fallbacks, `VAR="${A:-${B:-}}" cmd ...`, sets `VAR` to the **empty string** when neither source is set. That is not the same as leaving it unset, and for a tool that branches on *presence* rather than truthiness it is worse than both.
- Measured 2026-07-31 (rfp#93): rasterio tests `"PROJ_LIB" in os.environ` — a membership test an empty string passes — then calls `set_proj_data_search_path("")`, suppressing its own bundled `proj_data`:
  ```
  PROJ_LIB=   rio warp ... -> Error: Cannot find proj.db
  (unset)     rio warp ... -> EPSG resolves normally
  ```
  It surfaced as a missing-dependency error, not a quoting bug, and only on installs whose PROJ layout the caller could not introspect — so the fallback chain looked like the culprit.
- Same shape wherever presence is the test rather than value: Python `os.environ`, bash `[ -v VAR ]`, R `Sys.getenv(x, unset = NA)`.
- Fix — build the command as an array, add the assignment only when there is a value:
  ```bash
  cmd=("$TOOL")
  if [ -n "${MY_VAR:-}" ]; then cmd=(env "REAL_VAR=$MY_VAR" "${cmd[@]}"); fi
  "${cmd[@]}" ...
  ```
- Do not write `[ -n "$X" ] && arr=(...)` as a bare top-level list: under `set -e` a false test makes the list return non-zero and aborts the script. Use an explicit `if`.

### Parallel writers sharing one output file interleave mid-record
- `xargs -P N ... >> shared_file` (or any fan-out where N processes append to the same fd/path) is only safe while each record fits in a single `write()`. O_APPEND makes individual `write()` calls atomic, but a large record (anything beyond pipe/stdio buffer size, ~64 KB) spans multiple writes — concurrent jobs interleave mid-record and corrupt the file.
- The trap is latent: small records never trip it, so the pattern looks proven until the first large payload arrives. Caught 2026-07-11 in rtj's `stac_register-pypgstac.sh` — 20 parallel `curl | jq -c` jobs appending STAC items to one NDJSON worked for every prior collection (KB-scale items), then 9 MB floodplain items interleaved and produced an orjson decode error ~864 KB into line 1.
- Fix pattern: each parallel job writes its own temp file (unique name, e.g. md5 of the input), concatenate after the fan-out completes:
  ```bash
  cat urls.txt | xargs -P 20 -I {} fetch_one.sh {} "$OUT_DIR"   # each writes $OUT_DIR/<md5>.json
  cat "$OUT_DIR"/*.json > combined.ndjson
  ```
- Pair with a count guard — parallel `curl` failures under xargs are also silent: `[ "$(wc -l < combined.ndjson)" -eq "$EXPECTED" ] || exit 1` before any downstream load.

### `mktemp` template needs enough X's, and a failed `mktemp` leaves an empty var
- BSD/macOS `mktemp -d -t <name>` requires the template to contain at least 3 `X`s (`XXXXXX` is the safe default). Without them, mktemp errors to stderr (`too few X's in template`) and **prints nothing to stdout**.
- Pattern: `SCRATCH=$(mktemp -d -t aider-smoke) && cd "$SCRATCH" && <destructive>`. When mktemp fails, `$SCRATCH=""`. `cd ""` is a no-op that **leaves you in the caller's cwd**. The destructive command (`rm`, `git init`, `git add+commit`) then runs in cwd instead of a throwaway tmpdir.
- Caught the hard way 2026-05-13: a Claude smoke test inside the rtj checkout did exactly this, accidentally committed a `demo.R` to the active feature branch, which then rode the squash-merge into rtj/main and had to be cleaned up post-merge.
- Fix patterns:
  - Always use `XXXXXX` (6 X's) in the template: `mktemp -d -t aider-smoke.XXXXXX`.
  - Guard the result: `SCRATCH=$(mktemp -d ...) || exit 1; [ -n "$SCRATCH" ] || exit 1`.
  - Use `set -euo pipefail` so the failed command-substitution kills the script.

### BSD vs GNU sed/grep portability (macOS hits this constantly)
- macOS ships BSD `sed`/`grep`. Linux CI/cloud-init hosts ship GNU. Snippets that work on one silently misbehave on the other.
- **`\+` and `\|` are GNU BRE extensions.** On BSD they're treated as literal `+` and `|`, so the regex still "matches" but matches nothing useful — leaving raw input unchanged.
  - Symptom seen 2026-05-28: `sed 's/[^a-z0-9]\+/-/g'` on macOS left spaces in an issue-title slug, producing an invalid git branch name.
  - Fix: use `sed -E` (POSIX ERE) so `+`, `|`, `?`, `(...)` all work without escapes on both flavors. The same regex becomes `sed -E 's/[^a-z0-9]+/-/g'`.
- **`s|pat|repl|` delimiter conflicts with `|` in alternation/replacement on BSD.** Pick a delimiter that does not appear in pattern or replacement (`#`, `,`, `:` are common choices). Compound `s|x|y|; s|^| /||` chains where the trailing `||` looks like an empty delimiter break on BSD sed even when GNU accepts them.
- **Don't parse `ls`.** BSD `ls` emits ANSI colour codes when stdout is a TTY *or* when `CLICOLOR_FORCE` is set in env (often by shell rc files), and the codes leak through pipes. Downstream `grep`/`sed` chokes on the embedded escapes (`[01;31m...[0m`).
  - Use `find <dir> -maxdepth 1 -mindepth 1 -type d -exec basename {} \;` for directory listings, or `printf '%s\n' <dir>/*/` for a glob, or `for d in <dir>/*/; do basename "$d"; done`.
- **When writing a snippet you expect to ship in a `skills/` SKILL.md or any cloud-init runcmd**: it must be POSIX-portable. Default to `sed -E`, avoid `\+`/`\|`, and don't pipe `ls`.

### `gh` CLI
- **`gh pr create` resolves branch from CWD, not `--repo`**. Specifying `--repo NewGraphEnvironment/X` does NOT switch branch resolution — the command still reads the current working directory's checked-out branch. To open a PR in repo X, `cd` into X's checkout first, or pass `--head <branch>` explicitly.
- **`gh issue create` with heredoc bodies fails on prose containing special shell characters** (apostrophes, dollar signs, backticks). Use `--body-file /tmp/issue.md` instead — every project's `newgraph.md` convention specifies this; codified here for the underlying class.
- **Before `gh pr merge`, verify the branch is fully pushed.** `gh pr merge` merges the REMOTE branch — commits made locally but never pushed are silently excluded, so the PR merges "successfully" while `main` is missing work you know you committed. Check `git status -sb` shows no `ahead N` before merging (or that `git rev-list --count @{u}..HEAD` is 0). Worse: if you then delete the local branch (`--delete-branch`, or a follow-up `git branch -D`), the unpushed commits become **dangling** — recoverable via `git reflog` / `git fsck --lost-found` then `git cherry-pick`, but only if you notice they're missing. Caught twice 2026-07 in `floodplains`: PR #6 merged 1 of 3 branch commits (the drift#34 `changes_only` fix + a CLAUDE.md update were unpushed → stranded as danglers → recovered and re-merged via a follow-up PR); a second branch sat 4-ahead-unpushed at compact time. The same check belongs in the `gh-pr-merge` skill's pre-merge step.

### Process Visibility
- Secrets passed as command-line args are visible in `ps aux`
- Use env files, stdin pipes, or temp files with `chmod 600` instead

## Cloud-Init (YAML)

### ASCII
- Must be pure ASCII — em dashes, curly quotes, arrows cause silent parse failure
- Check with: `perl -ne 'print "$.: $_" if /[^\x00-\x7F]/' file.yaml`

### YAML flow-mapping in runcmd
- Any runcmd item containing both `{` and `:` is at risk of being parsed as a YAML flow-mapping (dict), not a literal string. Cloud-init's shellify hits a non-string and throws TypeError, **aborting all subsequent runcmd steps silently** while `final_message` still fires.
- Don't write: `- test -s /file || { echo "FATAL: ..." }` — the `:` inside braces makes YAML see a dict.
- Do write: use `- |` block scalar with explicit `if/then/fi`:
  ```yaml
  - |
    if [ ! -s /file ]; then
      echo "FATAL: ..." >&2
      exit 1
    fi
  ```
- Validate post-edit: `python3 -c "import yaml; runcmd=yaml.safe_load(open('cloud-init.yaml').read().split(chr(10),1)[1])['runcmd']; print([type(x).__name__ for x in runcmd if not isinstance(x,str)] or 'all strings')"`. If the output is anything other than `all strings`, the runcmd will fail.

### State
- `cloud-init clean` causes full re-provisioning on next boot — almost never what you want before snapshot
- Use `tailscale logout` not `tailscale down` before snapshot (deregister vs disconnect)
- Wipe `/var/lib/tailscale/*` before snapshot too — `tailscale logout` deauthorizes server-side but local node identity blob persists in tailscaled.state. Snapshot restored elsewhere inherits prior key material until `tailscale up` runs again.
- Wipe `/etc/ssh/ssh_host_*` before snapshot — otherwise droplets spawned from the same image share host identity.

### Template Variables
- Secrets rendered via `templatefile()` are readable at `169.254.169.254` metadata endpoint
- Acceptable for ephemeral machines, document the tradeoff
- Heredocs in runcmd that write secrets: `<<'EOF'` (quoted) prevents bash from re-expanding `$X` sequences in already-substituted credential strings. AWS keys rarely contain `$` but base64-padded secrets might.

### Repo + key install ordering
- `apt-key adv --keyserver` is deprecated on Ubuntu 24.04 noble — silently fails AND APT ignores resulting keyring. Use `gpg --dearmor` + `signed-by=` keyring file pattern.
- Repo .list files in `write_files:` trigger the implicit `package_update` BEFORE runcmd installs the keyring → first apt-get update fails with NO_PUBKEY. Put the repo line in runcmd alongside the key install, not in write_files.

### Cloud-init users vs DO SSH key injection
- DO injects `ssh_key_ids` only into `/root/.ssh/authorized_keys` (cloud-init's `cc_ssh` module). Cloud-init `users:` block with `ssh_authorized_keys: []` does NOT pick those up.
- Non-root users that need SSH access must copy from root's keys in runcmd:
  ```yaml
  - mkdir -p /home/<user>/.ssh
  - cp /root/.ssh/authorized_keys /home/<user>/.ssh/authorized_keys
  - chown -R <user>:<user> /home/<user>/.ssh
  ```
- Guard with `test -s /root/.ssh/authorized_keys` to fail loudly if `cc_ssh` hasn't run before runcmd (rare race).

## Spatial CLIs (bcdata, ogr, gdal)

### Negative coordinates get parsed as CLI options — every BC bbox hits this
- BC longitudes are all negative, so `--bounds -124.73 49.485 -124.595 49.565` fails with `Error: No such option: -1`. The parser sees a leading `-` and reads it as a flag. Affects click/argparse-based tools generally, not just bcdata.
- Use the **bracketed single-argument form with `=`**: `--bounds="[-124.73, 49.485, -124.595, 49.565]"`. The `=` keeps the value attached to the option, and the brackets keep it one token. A bare comma-joined string (`--bounds "-124.73,49.485,..."`) is not equivalent — it threw an unrelated traceback.
- Same class: any CLI taking negative numbers (elevation offsets, `--nodata -9999`, buffer distances). Reach for `--opt=value` by default rather than discovering it per-tool.

### bcdata: an empty result raises AttributeError, it does not return an empty collection
- A bbox query matching nothing exits non-zero with `AttributeError: You are calling a geospatial method on the GeoDataFrame, but the active geometry column to use has not been set.` — geopandas complaining about an empty frame, several layers below the query.
- The trap: that reads as a broken query, not as "zero features," so a real and meaningful **absence** looks like tooling failure. Don't conclude a layer is unavailable from this error.
- **Prove absence before acting on it.** Re-run the same query against a wider bbox known to contain features; if that returns rows, the empty result is real data. Caught 2026-08-22 establishing that BC's FTEN trail layers are genuinely empty over an entire island — the wider-box control returned 851 features, which is what turned "the query is broken" into "the province has no trails here."
- Wrap counts defensively: `try: json.load(...)` around the parse, and treat the failure as `0 features` only after the wider-box control passes.

## OpenTofu / Terraform

### State
- Parsing `tofu state show` text output is fragile — use `tofu output` instead
- Missing outputs that scripts need — add them to main.tf
- Snapshot/image IDs in tfvars after deleting the snapshot — stale reference

### Duplicate module blocks across envs double-track global resources
- A module instantiated in two env dirs (e.g. `module "iam"` in both `env/prod` and `env/dev`) means account-global resources (IAM users, roles) can be tracked in BOTH local states. Removing the module block from one env turns its state copies into pending DESTROYS — which would delete the real resource out from under the other env.
- Caught 2026-07-18 (rtj#185): `env/dev` state secretly held `role_terraform_awshak` — the role every `role-assume.sh` apply depends on — and a config cleanup turned it into a planned destroy.
- Fix: `tofu state rm '<addresses>'` in the env relinquishing ownership (no cloud change; auto-backs-up state), leaving exactly one owning env. Verify the resource survives (`aws iam get-role ...`).
- Review check: any plan that destroys resources in a shared/global-resource module → first confirm which OTHER env states track the same addresses (`grep <name> env/*/terraform.tfstate` or check the remote backend keys).

### Destructive Operations
- Validate resource IDs before destroy: `[ -n "$ID" ] || exit 1`
- `tofu destroy` without `-target` destroys everything including reserved IPs
- Snapshot ID extraction by name: use `awk -v n="$NAME" '$2 == n {print $1}'` (exact match on column 2). `grep -F "$NAME"` is substring-match and can grab a stale snapshot whose name contains the new name as a substring.

### "Has been deleted" in plan output is not authoritative — verify against the cloud API first
- The AWS provider (5.x and some 6.x) has a known class of bug where a transient read error (false 404, regional-endpoint hiccup) is interpreted as "resource deleted outside of OpenTofu." The plan will show the resource and any children scheduled for destroy + recreate (`forces replacement` cascades through children that interpolate the parent's id/arn).
- If you didn't delete the resource and the plan says it's gone, **verify against the cloud API before applying**: `aws s3 head-bucket --bucket X`, `aws iam get-role --role-name X`, etc. A `tofu plan -refresh=true` re-run a moment later often reports "No changes."
- Caught 2026-05-14 in rtj env/prod for stac-era5-land: bucket fully intact (60 objects, 307 MB) but plan said deleted with 5 child resources "must be replaced." Apply would have clobbered the policy + lifecycle configs against the still-existing bucket. Recovery via `-target` on the unrelated resource being added (rtj#157 then codifies `lifecycle { prevent_destroy = true }` on the bucket + load-bearing children).
- **Belt-and-suspenders defense:** add `lifecycle { prevent_destroy = true }` to high-value resources (S3 buckets, RDS instances, anything irreplaceable) in their module. Tofu will refuse to plan a destroy until the lifecycle line itself is removed in config — converts the failure mode from "apply silently clobbers" into "plan errors with `Instance cannot be destroyed`." Don't apply it to count-based resources where `count: 1 → 0` is a legitimate transition.

### Check IaC ownership before CLI-mutating cloud config
- Before changing bucket policies, lifecycle rules, IAM policies, etc. with the aws CLI, grep the Terraform modules for the resource. If tofu owns it, a CLI change is not "drift" — it is **reverted on the next apply** (silent rule deletion). `put-bucket-lifecycle-configuration` additionally REPLACES the whole config, so a CLI "add one rule" can also clobber tofu-owned rules immediately.
- Caught 2026-07-18 (water-temp-bc#23): a NoncurrentVersionExpiration rule was one `aws s3api put` away from being applied — rtj `modules/s3` owns `aws_s3_bucket_lifecycle_configuration`, so it would have first clobbered the IA-transition rule, then been reverted. Correct path was a module variable + `tofu apply` (rtj#187).
- Corollary: when a pipeline's write pattern evolves (append-only → rewrite-in-place), **re-audit the IAM verbs its role actually needs**. water-temp-bc's GHA role lacked `s3:DeleteObject`; the first compaction run half-applied a `sync --delete` and left the store with duplicate keys until manually repaired (rtj#147 reopened). Check for an existing module toggle first — `allow_delete` already existed.

## DigitalOcean

### Snapshot disk-size constraint
- DO snapshots include the source droplet's disk size. New droplets from a snapshot must have disk **>=** snapshot disk. Resize **up** is fine; resize **down** below the snapshot disk is impossible without rebuilding.
- Build the snapshot at the smallest droplet size you'd ever want to spin from it. Sizes vs disks at writing: `g-4vcpu-16gb` = 50 GB, `g-8vcpu-32gb` / `m-4vcpu-32gb` = 100 GB, `m-8vcpu-64gb` = 200 GB.
- If your workload requires X GB RAM minimum, your snapshot floor is whatever droplet has X GB AND the smallest disk class.

### Reserved IP detach behavior
- Targeted destroy (`tofu destroy -target=module.droplet -target=...assignment...`) preserves the reserved IP at $4/mo. Full `tofu destroy` releases it (next apply gets a NEW IP).

### Reserved IP assignment race (rtj#55, rtj#85)
- DO returns 422 "Droplet already has a pending event" when reserved IP assignment fires immediately after droplet+firewall creation. The droplet's internal event queue takes time to drain.
- **Every DO droplet module that uses a reserved IP MUST have:**
  1. `time_sleep` resource between droplet creation and IP assignment, with `create_duration ≥ 60s` (10s and 30s have both been observed to race; 60s has more headroom)
  2. `depends_on = [time_sleep.<name>]` on the `digitalocean_reserved_ip_assignment` resource
  3. A retry fallback in the wrapping shell script (`up.sh` style) that detects the 422 in tofu output and uses `doctl compute reserved-ip-action assign <ip> <droplet-id>` to recover. Tofu doesn't retry; it leaves state half-applied (assignment recorded but DO didn't actually attach).
- **Snapshot-based spins are MORE prone to the race** than first-boot from blank Ubuntu (more startup events compete for the droplet's event queue).
- **Audit existing modules:** `grep -L 'time_sleep' env/do/*/<host>/main.tf` finds modules missing the gate. As of 2026-05-02, openclaw and geoserv have no `time_sleep` — they will race eventually.
- **`depends_on` alone does not re-create the gate on a replace.** A `time_sleep` with `depends_on` but no `triggers` stays untouched in state when the droplet is replaced (`tofu apply -replace=...`), so the settle delay silently doesn't run and the reassignment races anyway. Verified empirically on OpenTofu 1.12.0. A *targeted destroy* does sweep dependents, so `tofu destroy -target=module.droplet` + `apply` is safe while `-replace` is not. Add `triggers = { droplet_id = module.droplet.id }` to close it, and prefer targeted destroy in any documented rebuild recipe.

### SSH keys apply at droplet creation only — guard the ForceNew edit
- DO injects `ssh_key_ids` into `/root/.ssh/authorized_keys` **once, at first boot** (cloud-init's `cc_ssh`) and never revisits the list. A key registered after a droplet was built therefore never reaches it, no matter what tfvars says. Symptom: a machine that reaches freshly-built hosts fine is denied by an older one.
- `ssh_keys` is **`ForceNew: true`** in the DO provider (a `TypeSet`, so reordering is safe). "Just add the key to tfvars" therefore plans a **destroy/recreate of the running host** — and doesn't even grant the new machine access to the host it destroyed. On a production database or tile server that is a catastrophe dressed as a one-line fix.
- **Guard the shared droplet module** with `lifecycle { ignore_changes = [ssh_keys] }`. It is safe precisely because DO cannot apply the change anyway: the only possible effect of that diff is an unwanted replace. `ignore_changes` governs updates only — creates and replaces recompute from config, so a deliberate rebuild still picks up the current list.
- Document the tradeoff where operators hit it: after the guard, editing `ssh_key_ids` produces **no plan diff at all**, and a typo'd key ID surfaces at create rather than at plan.
- To authorize a machine on a running droplet, append its pubkey — with `printf '\n%s\n'`, never `printf '%s\n'`. If the remote `authorized_keys` lacks a trailing newline (common once anyone has appended by hand), a bare append concatenates onto the last line and invalidates **both** keys — locking you out via the procedure meant to prevent lockout. `ssh-copy-id` handles this correctly.
- Check *which* file. DO's injection targets root only. A non-root SSH user has keys only if that env's cloud-init explicitly copied them at first boot — and that copy is one-time, so appending to root's file later grants the non-root user nothing.
- Caught in rtj#193: one machine had no path to a production STAC host for months because its key was registered after the droplet was built, and the obvious remediation would have destroyed the host.

## Docker / Postgres

### Postgis init time
- `imresamu/postgis` (and similar postgis images) on first cold start (empty data volume) take **5-12 min** to install all extensions — varies with disk IO and noisy-neighbor lottery on cloud hosts. Health-wait scripts must allow 15 min minimum, ideally with hard-fail + log dump on timeout.

### Tuning vs host RAM
- fresh's `docker/docker-compose.yml` defaults are tuned for a 128 GB host (`shared_buffers=32GB`, `shm_size=36gb`). On smaller hosts, postgres OOMs at startup with "could not map anonymous shared memory".
- 32 GB host floor: use the M1/cypher 32 GB-host preset (`scripts/fwapg/compose.override.m1.yml`) which sets `shared_buffers=8GB, shm_size=12gb`.
- Below 32 GB: postgres can technically start with smaller `shared_buffers` but fwapg work becomes painful. Don't run fwapg pipelines on <32 GB hosts.

### `search_path` is data, not config
- `ALTER DATABASE <db> SET search_path TO ...` is a database-level setting **stored in the postgres data dir**. Wiped with `docker compose down -v`. Must be re-applied on every restore.
- Codify in your restore script, not in cloud-init or compose env (those don't apply to db-level settings).

### `pkill <R/Python/etc. client>` does NOT cancel its Postgres query
- Killing the client (R, Python, psql) closes its connection. The libpq backend on the server keeps running the in-flight query until it finishes — **server-side orphan**. The orphaned backend holds whatever locks it had (table, view, advisory). Every later `DROP VIEW` / `LOCK TABLE` / `ALTER` on the same object blocks behind it indefinitely — *silent hangs* indistinguishable from a slow query.
- Caught 2026-05-25 in link#205: a `pkill`'d `wsg_run_one.R` left a `frs_network_features` SELECT running 1h45m; subsequent recomputes wedged on `DROP VIEW barriers_bt_access` for 1h08m before someone noticed.
- **Always terminate the server-side backend**, not just the client:
  ```sql
  SELECT pid, pg_terminate_backend(pid)
  FROM pg_stat_activity
  WHERE datname='<db>' AND state='active' AND now()-query_start > interval '3 minutes'
    AND pid <> pg_backend_pid();
  ```
  Then kill the client. Order matters when you don't know which side will block.

### Set `statement_timeout` + `lock_timeout` on long DB ops
- Any long-running DB op from an R/Python/etc. client should set both at session start, ideally via env (`PGOPTIONS='-c statement_timeout=600000 -c lock_timeout=60000'`) or on the connection itself (`DBI::dbExecute(conn, "SET statement_timeout = '600000'")`). A runaway query then cancels server-side (no orphan); a blocked `DROP VIEW` gives up rather than wedging behind a zombie lock. Without it, silent hangs become indistinguishable from "still working" and you wait hours.
- Pick a generous-but-bounded timeout (10× expected query time). The point isn't tight enforcement — it's "fail loud instead of fail silent."

### Function-as-join-predicate: index visibility depends on inlineability
- `JOIN b ON some_function(a.cols, b.cols)` — Postgres can only use the underlying indexes if `some_function` is `LANGUAGE sql` (inlineable). `plpgsql` functions are opaque and force per-row evaluation → seq scan / nested loop without indexes. Verify with `\df+ <function>` (look at `Language`) and `EXPLAIN` (look for the function body expanded into Filter / Index Cond).
- Caught in link#205 with `whse_basemapping.fwa_downstream` — it IS `LANGUAGE sql` + the planner did inline it; the symptom was elsewhere (see below). But if a function-based join is slow and the function is plpgsql, that's the first thing to look at.

### Joining on a per-tenant key (e.g. `id_segment` per-WSG) against a multi-tenant table is cartesian
- `id_segment` in link's persist schema is unique *within* a WSG, not globally (link#203). `WHERE id_segment IN (SELECT id_segment FROM streams WHERE wsg=aoi)` against persist matches access rows from *every* WSG sharing those id_segment values → N(WSGs)× duplicates → PK violations downstream and 50× memory.
- Fix: filter by the full tenant key (`watershed_group_code = aoi`) when the table has it. Pattern: introspect via `information_schema.columns` at runtime and branch — the same function can serve a working schema (single tenant, no WSG col) and persist (multi-tenant, with WSG col).

### View vs. real table changes the planner's join direction
- A `CREATE VIEW v AS SELECT * FROM big_table WHERE … ` carries no row-count statistics. Used as a join input, the planner may pick the other side (big) as the outer driver, blowing nested-loop cost ~1000× — the symptom looks like "the indexes aren't being used" but it's actually a wrong-direction nested loop.
- Caught in link#205: AOI-scoping streams via a `VIEW` left Postgres thinking the 26k FINA segments were as big as the 800k persist barriers; it picked barriers as outer; 71M estimated result rows; >10 min wall.
- Fix when AOI-scoping into a smaller dataset: **materialise as a real `CREATE TABLE` with indexes + `ANALYZE`**. The planner then sees the small row count and picks it as outer. Drop the table on `on.exit` if it's transient.

### Two-statement DELETE/INSERT into a persist table is not atomic
- A "DELETE WHERE wsg='X'; INSERT …" pair into a persist table from an orchestration script: if the INSERT fails (e.g. duplicate key from a subtle JOIN bug), the DELETE already ran → **data loss for that WSG**. Wrap in a single transaction (`BEGIN; … ; COMMIT`) when the persist table is the only source of truth, so a failed INSERT rolls back the DELETE. (link#205 lost FINA's `streams_mapping_code` to this; the surrounding cheap-recompute orchestration in `wsg_recompute_one.R` should wrap both statements in a tx.)

## Tailscale

### ACL "users" semantics
- Tailscale SSH ACL `"users": ["autogroup:nonroot"]` for `tag:compute` blocks `ssh root@<node>` over the tailnet. Use `ssh <user>@<node>` + sudo for root operations.
- For SSH-as-root from off-tailnet (regular OpenSSH on the public IP), the ACL doesn't apply — but you need the SSH key registered on the node.

### Reusable + ephemeral auth keys
- Cypher-style ephemeral compute droplets need both flags on the auth key: **Reusable** (same key works across destroy/recreate) + **Ephemeral** (tailnet entries auto-clean when offline >5 min).
- Tag the key (e.g. `tag:compute`) at creation time. Nodes joining with that key inherit the tag automatically — no `--advertise-tags` needed at `tailscale up` time.

## Security

### Secrets in Committed Files
- `.tfvars` must be gitignored (contains tokens, passwords)
- `.tfvars.example` should have all variables with empty/placeholder values
- Sensitive variables need `sensitive = true` in variables.tf

### Firewall Defaults
- `0.0.0.0/0` for SSH is world-open — document if intentional
- If access is gated by Tailscale, say so explicitly

### Credentials
- Passwords with special chars (`'`, `"`, `$`, `!`) break naive shell quoting
- `printf '%q'` escapes values for shell safety
- Temp files for secrets: create with `chmod 600`, delete after use

### Gitleaks pre-commit hook
Configuration patterns and false-positive handling for the `gitleaks` pre-commit hook (kdot's Brewfile ships `gitleaks` + `pre-commit`; cyclops standardizes the hook):
- **`.gitleaks.toml` schema in v8.30+**: top-level table is `[[allowlists]]` (PLURAL, array of tables). Each entry MUST include at least one of `commits` / `paths` / `regexes` / `stopwords`. The singular `[allowlist]` and `fingerprints = [...]` forms shown in older docs fail to validate. Use `paths` + `regexes` together for targeted file-and-content allowlists. Example in `soul/.gitleaks.toml`.
- **PEM marker regex spans multi-line**: gitleaks's `private-key` rule is `(?i)-----BEGIN...PRIVATE KEY-----[\s\S]*-----END...-----`. It matches across comment prefixes, blank lines, and code-fence boundaries. **Commenting out the markers does NOT neutralize the match.** Only fix in content is to omit the literal `-----BEGIN/END...-----` strings entirely and replace with prose ("Paste your private key here, preserving headers" etc.). See the `rtj` cypher `tfvars.example` precedent.
- **`curl-auth-header` rule false-positives on non-auth headers**: matches any `-H "X: Y"` shape, not just credential-bearing headers. Trips on docs with custom CORS or app-specific headers (e.g. `Zotero-Allowed-Request: true`). Fix: targeted `[[allowlists]]` with `paths` + `regexes`. Don't path-allowlist the whole file unless content is entirely safe.
- **`pre-commit install` legacy-hook handling**: running `pre-commit install` on a repo with an existing `.git/hooks/pre-commit` renames it to `.legacy` and keeps invoking it after framework hooks. No breakage, but means hook surface is split between `.pre-commit-config.yaml` and `.git/hooks/pre-commit.legacy`. For full visibility, migrate the legacy check into `.pre-commit-config.yaml` as a `local` hook so the whole hook surface is declared in one place.
- **AWS canonical example keys are allowlisted by default** (`AKIAIOSFODNN7EXAMPLE` etc.) — don't use those in test fixtures expecting a block. Use `ghp_`-shape PAT lookalikes or other non-allowlisted patterns for hook-trigger tests.

### "Public bucket" ≠ listable: GetObject vs ListBucket
- A bucket policy granting only `s3:GetObject` on `bucket/*` makes exact-key fetches public but NOT listing — and dataset discovery (`arrow::open_dataset()`, duckdb globs, STAC `/vsicurl/` directory reads) requires `s3:ListBucket` on the **bucket ARN** (no `/*`; it's a bucket-level action).
- The breakage hides: anyone with ANY ambient AWS credentials lists fine, so "anonymous access works" goes unverified for years. Caught 2026-07-18 (water-temp-bc#23 → rtj#187): anonymous `open_dataset()` had never worked on a bucket whose whole purpose was credential-less querying.
- Review checks: for an open-data bucket, the policy needs BOTH statements (GetObject on `bucket/*`, ListBucket on `bucket`); acceptance-test anonymous access from a credential-stripped environment (`env -u AWS_ACCESS_KEY_ID ... AWS_CONFIG_FILE=/dev/null`). Note ListBucket makes the full key listing publicly enumerable — intended for open data, wrong for mixed-content buckets.

## R / Package Installation

### `glue()` trims common leading whitespace
- `glue::glue()` strips the common indentation of its input, so a template whose
  output must preserve exact indentation (XML, YAML, Makefiles, Python) comes
  out subtly wrong — valid-looking, wrongly indented.
- For those blocks use a raw string with a `gsub()` placeholder instead of a
  glue template. Seen in rfp's QML form builder, where the photo widget's XML
  indentation has to survive verbatim.
- Related, and the opposite mistake: glue does **not** re-parse interpolated
  values, so literal `{...}` inside a *value* is safe. Don't rewrite a working
  generator to escape braces that were never a problem — probe it first.

### `on.exit()` at a script's top level never fires
- `on.exit()` registers a handler on the *current frame*. At the top level of a
  file run with `Rscript`, that frame is the global environment, which never
  exits — so the handler is registered and then simply never called.
- It looks correct, and it is correct inside a function. The failure is silent
  and, when the thing being cleaned up lives outside the repo, invisible to
  `git status`: rfp accumulated six staging directories in `$HOME` before anyone
  noticed, from two different scripts that both looked right.
- Use `withr::defer(cleanup, envir = globalenv())`, which registers a finalizer
  that runs at session end. It prints `Ran 1/1 deferred expressions` — that line
  in script output is the confirmation it worked, not noise.
- Probe rather than assume when checking this: a cleanup target inside
  `tempdir()` is removed by R's own session cleanup regardless, so testing there
  reports success for both the working and broken versions.

### A `data-raw/` script must load the source tree, not the installed package
- `requireNamespace("pkg")` succeeds whenever **any** version is installed, so a
  guard shaped like `if (!requireNamespace("pkg")) pkgload::load_all()` silently
  runs against the installed one. A generation script operates on the source
  tree by definition; reading a different copy of the package to do it is the
  bug.
- The gap is routinely enormous and nobody notices, because nothing errors.
  Measured in rfp: the installed package was **sixteen releases behind** the
  working branch, with a lookup table missing a whole row and an internal
  constant missing three entries.
- It fails quietly in both directions. One script iterated the stale lookup and
  **skipped an item entirely**, reporting 11 where the source had 12. Another
  generated two committed artifacts through a stale scan; those artifacts turned
  out byte-identical when regenerated correctly, but only because the input data
  happened not to exercise the missing entries — the same accident that let the
  original bug ship.
- Fix: `pkgload::load_all(quiet = TRUE)` **unconditionally**, and call functions
  unqualified. `pkg::` and `pkg:::` in a `data-raw/` script reach the installed
  namespace and defeat the point.
- Check for it by asserting a count the script should cover:
  `nrow(registry)` against items processed. A silent skip is invisible otherwise.

### `lintr` also resolves against the installed package, not the source tree
- The same installed-vs-source trap as the `data-raw` case above, in a tool
  where it reads as a code defect rather than a stale dependency.
  `object_usage_linter` resolves a package-level object through the installed
  namespace, so **every internal constant added on the current branch** is
  reported as `no visible binding for global variable`.
- It is convincing because the surrounding constants resolve fine — they are in
  the installed copy. Confirm before "fixing" anything:
  ```r
  exists(".my_new_constant", asNamespace("pkg"))   # FALSE  -> lint artifact
  exists(".an_old_constant", asNamespace("pkg"))   # TRUE
  ```
  If the new one is absent from the installed namespace and the old one is
  present, the warning clears on reinstall and there is nothing to change.
- Corollary for reading a lint report at all: **compare against the baseline
  before treating a count as signal.** Lint the file as it stands at `HEAD`
  (`git show HEAD:R/f.R > /tmp/f.R`) and diff the counts by linter. A file that
  already carried 26 lints in the repo's prevailing style is not a file your
  change made worse.
- And check whether the repo has a `.lintr` at all. Without one, `lint_package()`
  runs the strict defaults, which disagree with tidyverse continuation-indent
  style on essentially every wrapped call — hundreds of hits that are house
  style, not defects.

### Regenerated binaries churn git even when nothing changed
- Formats that embed a creation timestamp or other run-varying metadata produce
  a different file on every rebuild. An unconditional write then puts a binary
  diff in every commit, and a real change becomes invisible among the noise.
- GeoPackage is the live case: `gpkg_contents.last_change` made a ~100 KB file
  churn on each rebuild of an unchanged form.
- **Write to a temp file, compare the things that actually matter, replace only
  on a real difference.** Choose the comparison deliberately — for a GPKG that
  is `PRAGMA table_info` **plus** geometry type **plus** CRS, because CRS lives
  outside the column list and comparing columns alone silently keeps a stale
  projection. Then a file appearing in the diff means something genuinely changed.
- Text artifacts that are byte-stable can just be rewritten every time; the
  guard is only worth it where the format is not.

### A drift guard must cover every input it claims to
- Guards that assert "nothing has been added without a decision" are only worth
  their maintenance if they walk **all** the inputs. One that checks a subset
  gives the same green signal while the uncovered part drifts freely.
- Enumerate the source of truth programmatically rather than listing what you
  remember: walk the registry / schema / directory, diff it against the declared
  set, and fail on anything in neither "handled" nor "deliberately ignored".
- Require a **reason** on every ignored entry. An ignored item without one is a
  backlog note pretending to be a decision, and it gets re-litigated at every
  review.
- Then prove the alarm can fire: feed it a deliberately undeclared input and
  assert it is reported. A guard nobody has seen fail is decoration.

### pak Behavior
- pak stops on first unresolvable package — all subsequent packages are skipped
- Removed CRAN packages (like `leaflet.extras`) must move to GitHub source
- PPPM binaries may lag a few hours behind new CRAN releases

### Reproducibility
- Branch pins (`pkg@branch`) are not reproducible — document why used
- Pinned download URLs (RStudio .deb) go stale — document where to update

### `R CMD build` ships every top-level directory not in `.Rbuildignore`
- Internal coordination directories — `comms/`, `research/`, `planning/`, `dev/` — land in the tarball and therefore in the library of anyone installing from GitHub. `R CMD check` only flags this as a NOTE ("Non-standard files/directories found at top level"), which is easy to scroll past among the notes you have decided to live with.
- `.gitignore` does **not** cover this. A locally-gitignored file (e.g. `.aider.chat.history.md`) is still picked up by `R CMD build`.
- The gap appears over time rather than at scaffold: found 2026-07-31 in rfp, where `planning`, `.claude`, `CLAUDE.md` and `dev` were all excluded but `comms` and `research` — added later — were not. 10 files of cross-repo coordination notes were shipping.
- This matters most for the three-layer repo split (see `newgraph.md`): `comms/` is internal-by-definition, so a public-flipped package that ships it leaks exactly what the flip was meant to purge.
- Audit every R repo at once:
  ```bash
  for d in ~/Projects/repo/*/; do
    [ -f "$d/DESCRIPTION" ] || continue
    for sub in comms research planning dev; do
      if [ -d "$d/$sub" ] && ! grep -qE "^\^${sub}\\\$" "$d/.Rbuildignore" 2>/dev/null; then
        echo "$(basename "$d") ships $sub/"
      fi
    done
  done
  ```
  Run 2026-07-31: 20 hits across 16 repos. `comms/` in `link`, `fish_passage_template_reporting`, `neexdzii_kwa_benthic_2025`; `research/` in `link`; the rest `planning/` or `dev/`.
- Verify a fix against the tarball, not the config — the `.Rbuildignore` regex is easy to get subtly wrong:
  ```bash
  R CMD build . >/dev/null && tar tzf pkg_*.tar.gz | grep -c '^pkg/comms/'   # expect 0
  ```

### Base name shadowing in formal args
- Avoid `names`, `length`, `data`, `c`, `t`, `T`, `F`, etc. as formal argument names. R's function-lookup fallback often rescues `names(x)` calls inside a function whose arg is also called `names` — but it's a confusing read, breaks under refactors, and generates a real "could not find function" error when the lookup heuristic misses (e.g. inside lapply/vapply/match.fun chains). Prefer descriptive alternatives: `label_names`, `n`, `df`, etc.
- Caught in mc#33 round 1 — `mc_label_ensure(names)` worked by luck when calling `names(existing)` to read a named-vector's names; renamed to `label_names` for safety.

### Cross-function consistency for label/string normalization
- When two functions in the same package both decide whether a string is a "system value" (or any normalized form), they MUST use the same comparison. Mismatches are silent bugs that surface only on edge cases.
- mc#33 example: `mc_label_ensure` used `toupper(nm) %in% sys` (case-insensitive system-label skip), but `resolve_label_names` used `nm %in% sys` (case-sensitive). Result: `add = "inbox"` with `create_missing = TRUE` was silently broken — ensure skipped creation, resolve couldn't match. Fix: both use `toupper(nm) %in% sys` and the resolver normalizes its return to the canonical case.
- Generalized check: when reviewing a diff that adds normalization (case, whitespace, prefix-trim) on one side of an interaction, grep for the other side and align them.

### Cache keys must cover every output-affecting input
- A file cache keyed by fewer inputs than the write depends on returns silently wrong data — the worst failure class: no error, plausible-looking output. Enumerate every parameter that changes the written artifact and put each in the key (or its hash). The safe failure direction is over-keying (spurious refetch), never under-keying.
- drift#25 example: `dft_stac_fetch()` cached STAC rasters as `<source>/<year>.nc` — no AOI in the key. A second watershed silently received the first watershed's raster masked to its own extent (~3% overlap looked plausible enough to almost ship). Fix: filename gains a hash over AOI geometry + `res`/`crs`/`dt`/`aggregation`/`resampling`/`stac_url`/`collection`/`asset`.
- Hash *resolved* values, not raw args: defaults filled from config (`%||%`) must resolve before hashing, or `f(x)` and `f(x, url = <same-as-default>)` key differently for identical output.
- R hashing gotchas (`rlang::hash()` serializes, so type and attributes matter):
  - sf geometry: hash WKB (`sf::st_as_binary(sf::st_geometry(x), endian = "little")`), not the sfc object — sfc carries a PROJ-generated CRS WKT that drifts across PROJ versions (spurious cache misses), and hashing a whole sf data.frame leaks attribute columns into the key. Pass the CRS string as a separate key member.
  - Coerce numeric types: `10L` and `10` hash differently — `as.numeric()` before hashing.
- Check the cache's `force`/refresh escape hatch actually overwrites: drift#25's `force = TRUE` errored on the existing file ("File already exists"), broken exactly when needed. Prefer the writer's explicit `overwrite = TRUE` arg over a bare `unlink()` — unlink fails silently on Windows under an open file handle.

### terra: operator dispatch and edge cases in package code
- **SpatRaster `%in%` is not dispatched when terra is *imported* (only when *attached*).** Inside a package (terra in `Imports`, used via `::`), `some_raster %in% vec` falls through to base `match()` and errors with `'match' requires vector arguments`. A `library(terra)` smoke test passes (attaching installs the S4 method), so the bug hides until package context. Use `terra::subst(x, from, to, others = ...)` or `terra::classify()` for code-set membership/masking instead of the `%in%` operator. Same trap for any operator terra defines via S4 that base also defines as an ordinary function. (drift#34)
- **`terra::freq()` errors on an all-NA raster** (`replacement has length zero`) rather than returning a 0-row table. Any path that can yield an all-NA layer (an impossible filter, everything masked out) must guard: `f <- tryCatch(terra::freq(r), error = function(e) NULL)`, then treat `NULL`/0 rows as "no values". Don't assume the empty case gives `nrow(freq(r)) == 0`. (drift#34)

### sf: `st_join(largest = TRUE)` ignores the join predicate
- `sf::st_join(x, y, join = predicate, largest = TRUE)` does **not** use `predicate` to decide matches — with `largest = TRUE`, sf runs `st_intersection(x, y)` and keeps the feature of greatest overlap area, so matching is *always* intersection-based regardless of what `join =` is set to. A function that exposes a configurable predicate AND a largest-overlap mode therefore silently mis-attributes when both are combined: pass `st_within` expecting containment, get anything that merely *overlaps*. Verify against sf source, not the argument list — the `join` arg is accepted and ignored, not rejected. Fix: abort when a non-default predicate is combined with the largest-overlap mode, rather than honouring one and dropping the other. (drift#42)
- Corollary: `largest = TRUE` also drops zero-area geometries from consideration — so a predicate join against **point** or **line** overlays cannot use largest mode at all (no area to compare). Point/line attribution must go through the plain (`largest = FALSE`) predicate path.

### sf: name validation must account for the geometry column
- The active geometry column is a named entry in `names(x)`, but its name is **not fixed** — `"geometry"` from `sf::st_read()` of some sources, `"geom"` from a GeoPackage/PostGIS layer, `"geometry"` or `"_ogr_geometry_"` elsewhere. Code that validates user-supplied column names with `cols %in% names(x)` will happily accept the geometry column, then break downstream (`st_join` drops `y`'s geometry, so a requested "attribute" column silently never appears; a 0-row short-circuit path may instead attach a stray empty sfc). A same-name collision check across two sf objects also misses this when the two layers name their geometry differently. Guard explicitly with `attr(x, "sf_column")` — reject it from the caller-supplied column set. (drift#42)

### sf: reproject the polygon to get a lat/lon bbox, never transform the projected bbox corners
- To hand a geographic (EPSG:4326) bounding box to a bbox-filtered query (WFS/OGC features, `?bbox=`), reproject the whole AOI **geometry** then take its bbox: `sf::st_bbox(sf::st_transform(aoi, 4326))`. Do **not** compute the bbox in the projected CRS and transform its two corner points — a projected rectangle's edges bow under reprojection, so the corner-transformed box is skewed and generally too short on one axis. The pre-filter then silently under-covers the true extent: features inside the AOI but outside the shrunken box are never fetched, and a downstream clip can only *remove*, never recover them. Symptom: counts a few percent low near the north/south extremes of an area, with no error. A native-CRS bbox filter (e.g. ogr2ogr `-spat <bounds> -spat_srs EPSG:3005`) is unaffected — only the reproject-the-corners step is the bug. (rfp#12)

### arrow dplyr backend: no grouped slice — bridge to duckdb
- arrow's dplyr backend errors on grouped `slice_max`/`slice_min` (`arrow_not_supported("Slicing grouped data")`). The working pattern for any "latest per group" over parquet/S3: `arrow::open_dataset(...) |> dplyr::filter(...) |> arrow::to_duckdb() |> dplyr::group_by(...) |> dplyr::slice_max(...)`.
- The `to_duckdb()` bridge is also a return-type contract: helpers that return the lazy query should keep the bridge even when they no longer need it internally, or downstream callers using grouped verbs break. (water-temp-bc#17, #23)

### as.POSIXct.Date silently ignores tz=
- `as.POSIXct(x, tz = "UTC")` on a `Date` ignores `tz` and converts in the system local zone — west of UTC this shifts date boundaries by the local offset and silently drops edge data. Force UTC via `as.POSIXct(format(x), tz = "UTC")` when accepting Date inputs; widen Date upper bounds to `< next-day-midnight` so the whole calendar day is included. (water-temp-bc#17)

### open_dataset(unify_schemas = TRUE) requires aligned types
- Cross-prefix/file schema unification only merges what types allow: `timestamp[us, tz=UTC]` will not merge with naked `timestamp[us]`, `Grade: string` not with `Grade: double`. Audit the schemas of every file group BEFORE promising unified reads over a mixed archive; plan a normalization pass otherwise. (water-temp-bc#17)

### duckdb larger-than-memory dedup: shard the work — settings won't save you
- duckdb's **window operator** (QUALIFY row_number ...) does not spill enough to survive big partitions (OOM'd an 8 GB limit on a ~124M-row input). The **arg_max/struct-payload hash aggregate** cannot spill its state either (observed OOM with an empty temp dir). `preserve_insertion_order = false` and fewer threads help but do not fix it.
- **In-memory duckdb connections never offload to disk at all** — `SET temp_directory` on `dbConnect(duckdb())` is a no-op for operator spill. File-backed (`dbdir = <file>`) is required for any spilling.
- The structure that works at any scale: **hash-shard by a column inside the group key** (e.g. `hash(STATION_NUMBER) % K = k`, K = `ceiling(input_rows / shard_rows)`), one aggregation pass per shard, each writing its own ordered output file. A key never crosses shards, so dedup stays exact; memory scales 1/K. Extra passes cost scan time only — per-pass aggregate state is what OOMs, so when in doubt shard smaller. (water-temp-bc#23)
- **Local runs at the same duckdb `memory_limit`/`threads` do NOT validate a constrained runner.** 10M-row shards passed a Mac at the exact 4 GB / 2-thread settings but OOM'd the real 7 GB GHA runner (partition 46 squeaked through in 94s, 47 died 15s in) — abundant physical RAM masks how tight duckdb's accounting runs at its internal limit. Only the real runner is the real test; size shards with margin (water-temp-bc ships 6M), and treat a near-timeout/near-limit pass as a failure to fix, not a pass. (water-temp-bc#23 run 29675228557, fixed in PR #25)

### `nzchar(NA)` is TRUE — non-empty checks silently pass NA
- `nzchar(NA)` returns `TRUE`, so the natural "is this cell filled in" test — `all(nzchar(trimws(x)))` — waves through a column full of `NA`. `trimws(NA)` is `NA`, and `nzchar()` of that is `TRUE` unless you pass `keepNA = TRUE`.
- Use an explicit guard: `filled <- function(x) !is.na(x) & nzchar(trimws(x))`. Same trap in reverse for `read.csv()`, which yields `""` for an empty field but `NA` for a literal `NA` — so a file can fail one check and pass the other for the same visual blank.
- Bites hardest in validators, where the whole point is catching a half-authored row. (link#233, 2026-08: a dictionary contract test asserting every row carried a description would have passed on an entirely NA column.)

### Test fixtures must mirror production column TYPES, not just shapes
- A fixture-green suite can hide type bugs that only real data exposes: water-temp-bc#23's fixtures had `Grade` as string when production has double, so a `coalesce(Grade, '')` sentinel inside the dedup ordering passed all 27 tests and broke on first contact with real data.
- When writing fixtures for a pipeline over an existing dataset, print the real schema (`arrow::open_dataset(...)$schema`) and copy the types verbatim. Any type-sensitive expression (coalesce sentinels, casts, comparisons) is only tested if the fixture types match.

## General

### Two agent sessions must not share one git working tree — give each a worktree

- A git working tree has exactly **one** checked-out branch. When two concurrent Claude sessions operate in the same directory, either can `git checkout` out from under the other **mid-edit**. The victim's uncommitted work stays on disk but is now sitting on the *other* session's branch — so a later `git add`/`commit` silently lands it on the wrong branch, and a `--delete-branch` merge can strand it entirely.
- Symptoms: an `Edit` fails with "File does not exist" for a file you just wrote (their branch doesn't have it); `git branch --show-current` returns a branch you never created; your new files show as untracked on someone else's feature branch; `planning/active/` suddenly empty.
- Caught three times in one session (2026-07, floodplains): twice mid-implementation, and once while running a `--public-clean` scrub — the scrub committed to a parallel session's feature branch instead of `main`, which would have flipped the repo public with an **un-scrubbed `main`**. That third one is the dangerous class: the safety work (`.claude/visibility`, stripped internal conventions) sat on a branch nobody was about to merge.
- **Prevention:** one worktree per session — `git worktree add ../<repo>-<task> -b <branch>`. Each session gets its own directory and its own checked-out branch; no contention.
- **Detection (cheap; do it before any commit, merge, or visibility flip):** assert the branch is what you think it is, not just that the tree is clean.
  ```bash
  [ "$(git branch --show-current)" = "$EXPECTED_BRANCH" ] || { echo "WRONG BRANCH"; exit 1; }
  ```
- **Recovery:** back up the touched files first (`cp` to a scratch dir, `git diff > x.diff`), confirm the other branch's changes don't overlap yours (`git diff --name-only main..their-branch`), then `git checkout <your-branch>` — uncommitted changes carry across cleanly when there is no overlap. Commit **and push** immediately; an unpushed branch is what gets stranded. If you already committed onto their branch, restore their pointer with `git branch -f <their-branch> <their-last-sha>` (your commit stays reachable via reflog).

### Adopting Existing Config

When importing config from one location into a canonical one (legacy `~/.bash_profile` → dotfiles repo, old script's env → repo, another project's `settings.json` → soul):

- **Verify every referenced path/binary exists.** Dead PATH exports, missing interpreters, stale env vars should be cut, not codified.
  Shell paths: `for p in $(echo "$PATH" | tr ':' ' '); do [ -d "$p" ] || echo "DEAD: $p"; done`
- **Ask before dropping a reference** — it may be something the user forgot to reinstall on this machine, not something to delete.
- **Curated subset, not verbatim copy.** The diff should reflect what you verified, not the whole source.

### Test the cold/create path of idempotent code, not just the warm no-op
- Idempotent provisioning code (a resolver-file writer, a config installer, a "create unless present" block) has two paths: the **cold** path that actually creates/writes, and the **warm** path that detects "already present" and skips. They exercise almost-disjoint code.
- Testing only on a host where the artifact already exists hits **only the warm no-op** — which cannot catch any cold-path bug: missing-directory, a derivation that returns empty, a pipefail abort before the write, wrong permissions, a flush that never runs. The warm path's job is literally to do nothing, so a green warm test proves almost nothing about onboarding.
- Every fresh host runs the **cold** path — that's the one onboarding depends on. Test it deliberately: back up + remove the artifact, run cold, assert it was created correctly, then re-run to confirm the warm no-op. (Caught 2026-06-23 on rtj#75: the resolver-writer's first test plan only ran the warm path on a host that already had `/etc/resolver/<suffix>`; a Plan-agent review flagged that the cold path — the one every new host takes — was untested. Fixed by `sudo rm`-ing the file and running cold before close.)
- Generalizes beyond shell: any "ensure X exists / converge to desired state" operation — Terraform resources, migrations, package installs — wants the from-absent path tested, not just the already-converged re-run.

### A round-trip through your own reader proves nothing about interop
- When code writes a format some **other** program consumes — a database table, a config file, an export another tool imports — a test that writes then reads it back with your own reader validates only that you are self-consistent. It cannot detect that the real consumer rejects what you wrote.
- Symptom when wrong: every test green, the artifact byte-perfect on inspection, and the feature silently does nothing in production. Failures on the consumer's side are often **silent by design** — a lookup that matches nothing returns "no result", not an error.
- Get the real consumer into the loop, even if awkward: run it in a container, shell out to its CLI, gate the test on the tool being installed and skip otherwise. Then keep a cheap structural assertion alongside for CI, so the invariant is still guarded when the heavy test skips.
- Best ground truth is **the consumer's own output**: have it write the artifact once, then diff yours against it. That surfaces required fields no documentation mentions.
- (rfp#17, 2026-08: `layer_styles` rows were written with `f_table_schema` NULL. QGIS looks a style up with an equality match passing `""`, and `NULL = ''` is never true in SQL, so every row was invisible — `loadDefaultStyle()` returned FALSE, layers drew with default symbols, nothing logged. The rows round-tripped perfectly through DBI, so the whole suite was green. Found only by asking QGIS in a container, then bisecting against a row QGIS wrote itself.)

### Mocking the transport means the request is never built
- A network client mocked at its HTTP boundary — `local_mocked_bindings(.do_http = ...)`, `responses`, `nock`, a stubbed `fetch` — gives excellent coverage of *response* handling and **zero** coverage of the request. Status codes, retries, backoff, parse errors, partial bodies: all testable. Method, headers, content type, and body encoding: never exercised, because no test that stubs the transport constructs one.
- The gap is invisible in the usual way. The suite is green, the code reads correctly, and the first real call fails with a status that looks like the *server's* problem — a 400 or a 406 reads as a bad query or a rate limit long before it reads as "we sent the wrong content type".
- Sibling of the interop rule above, one level lower: that one is about what a consumer reads from your artifact, this one is about whether the request ever reaches the consumer at all.
- Fix pattern: make the wire format a **pure function** and assert it offline — `build_body(query)` returning a string, tested for its prefix, for a round-trip back through the decoder to the original input, and for no unescaped metacharacters surviving. Cheap, no network, and it guards the exact thing the mocks cannot.
- Verify the real encoding once against the live service and record the result, because the wrong choice is often the more obvious-looking API. (rfp#168, 2026-08: `curl::handle_setform()` reads like the way to send a form and sends `multipart/form-data`; the Overpass API answered **400** on every endpoint, having answered **406** to a raw body. Only `data=` url-encoded via `postfields` returns **200**. 130 tests passed while this was broken, and more of the same kind would not have helped.)

### A fixture set that cannot reach the failure mode is not validation
- Hand-picked fixtures test the cases you thought of. If every one of them is structurally incapable of triggering the bug class you are fixing, a green run means nothing — and it is *more* dangerous than no test, because it licenses the claim "validated".
- Before declaring a fix verified, ask what the fixtures have in common and whether that shared property is the very thing the bug depends on. If it is, the set has a hole no amount of additions to it will close.
- Prefer a **global structural invariant** over more examples. Properties like antisymmetry, transitivity, "every node reaches a terminal", or a conservation total sweep the whole domain and cannot be gamed by fixture choice.
- (link#227 / fresh#214, 2026-08: a watershed drainage-closure fix was declared validated on 8 hydrology fixtures. All 8 compared groups with *differing* stream codes — the bug only manifests between groups sharing one code, so the set could not have caught it. The very next case tried, the Fraser, dropped the group the entire basin drains through. What actually earned the claim was a transitivity sweep: 0 violations across 3,537 triples, plus 0 cycles and every group reaching an outlet.)

### Canonicalize serialized documents before diffing them
- XML and JSON emitters are free to vary attribute order, whitespace, and regenerated ids without changing meaning. Comparing two such documents raw reports differences that are not differences — and the noise scales with document size, so it looks like a real signal.
- Normalize first: C14N for XML (`ET.canonicalize(strip_text=True)` sorts attributes), key-sorted dumps for JSON, and mask any regenerated identifiers (uuids, timestamps, generator version stamps).
- Then narrow the mask deliberately. Every field you normalize away is a field the comparison can no longer catch, so name each one and why — a mask that quietly grows turns a drift guard into decoration.
- (rfp#17, 2026-08: comparing two QGIS templates raw said 5 of 43 shared layers still matched, which read as severe drift and argued for restructuring how styles were stored. Canonicalized — attributes sorted, symbol uuids masked — it was **46 of 47**. The templates had not drifted at all; the difference was attribute order between two QGIS builds. The naive number nearly bought an architecture change nobody needed.)

### Documentation Staleness
- Moving/renaming scripts: update CLAUDE.md, READMEs, usage comments
- New variables: update .tfvars.example
- New workflows: update relevant README


# NGE Feature Workflow

For non-trivial issue-driven work, follow this checklist. Each step exists for a reason — skipping leads to rework, broken builds, and avoidable bugs that we've hit repeatedly.

## The Sequence

1. **Start with `/planning-init <N>`** — given an issue number, enters plan mode for codebase exploration, presents a phase breakdown for user approval, then scaffolds branch + PWF baseline with the approved phases. One command replaces the manual issue → explore → plan → branch → scaffold dance.
2. **Write robust tests first** — failing tests that reproduce the issue or document the new behavior. Tests are the contract; they fail until the work makes them pass.
3. **Name with intent** — functions, parameters, internal helpers carry the naming style of the package they live in. Look at existing exports as the guide; consistency over cleverness. (Per-package naming convention TBD — see soul issue tracking.)
4. **Examples that run** — every exported function gets a runnable `@examples` block. Pkgdown renders them; CI executes them. An example that doesn't run is documentation rot.
5. **Code-check before each commit** — `/code-check` on staged diff. Catches what tests miss: edge cases, hard-coded paths, unguarded variables, security issues.
6. **Atomic commits** — each commit bundles code change + checkbox flip in `task_plan.md`. The diff and the progress live in the same commit; `git log -- planning/` tells the full story.
7. **`/planning-archive` when complete** — moves PWF to `archive/YYYY-MM-issue-N-slug/`, creates a fresh `active/`. Then `/gh-pr-push` opens the PR; `/gh-pr-merge` handles the release bookkeeping.

## When to Skip

For one-line typo fixes, version-bump-only PRs, or trivial documentation edits, the full workflow is overhead. Use judgment. The threshold is roughly: **multi-step issue, multi-file change, or anything that requires scoping** → use the workflow.

## Skills That Slot In

- `/planning-init <N>` — start
- `/planning-update` — sync checkboxes mid-session
- `/code-check` — before every commit
- `/planning-archive` — when issue closes
- `/gh-pr-push` — open the PR
- `/gh-pr-merge` — merge with release bookkeeping

## Why This Exists

We've hit snags repeatedly when half-doing this — branches that mix concerns, tests bolted on after, code-check skipped (and then a bug ships in the diff), examples that fail in pkgdown. Each step is small; the cumulative reliability gain is real. The convention is here so it becomes the default expectation, not a thing the user has to remind every session about.


# LLM Behavioral Guidelines

<!-- Source: https://github.com/forrestchang/andrej-karpathy-skills/main/CLAUDE.md -->
<!-- Last synced: 2026-02-06 -->
<!-- These principles are hardcoded locally. We do not curl at deploy time. -->
<!-- Periodically check the source for meaningful updates. -->

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## 5. Subagents Are Evidence, Not Dependencies

**Don't block on one. Don't trust its status. Verify its claims in both directions.**

### Don't block

Spawn a background subagent, then keep working on the lowest-risk part of the
task — scaffolding, data files, tests. When findings arrive, treat them as a
review of landed work rather than a precondition for starting it.

If a result genuinely must precede the next step, run it synchronously
(`run_in_background: false`) so the blocking is explicit and visible.

Three observed cases where waiting would have been the expensive choice:

- A research agent spawned 5 children and deadlocked for **~3 hours**, still
  reporting as "running". The user caught it, not the agent.
- A `Plan` agent asked to review a `task_plan.md` *before the baseline commit*
  returned after the issue was implemented, reviewed, merged and tagged.
- The same pattern on a later issue: findings arrived after all four phases had
  shipped. Because the work had not waited, this cost nothing — three findings
  were still new and landed as follow-up commits.

That last one is the shape to aim for. Concurrent review is not a degraded
version of blocking review; it is often better, because the reviewer reads real
code instead of a plan.

### Don't trust status

**Never report an agent as "still running" without evidence.** Agent status and
`TaskList` have both been observed to be wrong — `TaskList` reported "No tasks
found" for an agent that was alive and later replied. Check the output file's
mtime before claiming progress, and say what you checked.

### Verify claims, in both directions

Subagent output is evidence, not verdict. Both failure modes are real:

- **Acting on a wrong finding.** One labelled BLOCKER — "`glue()` will choke on
  the literal braces in this fragment" — was disproved by a 30-second probe,
  because glue does not re-parse interpolated values. Acting on it would have
  meant rewriting a working generator.
- **Dismissing a late review wholesale.** In that same review 2 of 9 findings
  were real, including a dead link. In a later one, a finding that a
  `path|layername=` check would delete KML/GPX layers was correct, and was
  confirmed against 207 real datasources before the fix landed.

The rule that separates them: **cheap probe first, then act.** Reproduce the
claim before you fix it, and before you dismiss it. A finding you cannot
reproduce is a finding you do not yet understand.


**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.


# Planning Conventions

How Claude manages structured planning for complex tasks using planning-with-files (PWF).

## When to Plan

Use PWF when a task has multiple phases, requires research, or involves more than ~5 tool calls. Triggers:
- User says "let's plan this", "plan mode", "use planning", or invokes `/planning-init`
- Complex issue work begins (multi-step, uncertain approach)
- Claude judges the task warrants structured tracking

Skip planning for single-file edits, quick fixes, or tasks with obvious next steps.

## The Workflow

1. **Explore first** — Enter plan mode (read-only). Read code, trace paths, understand the problem before proposing anything. When the work codifies a pattern that already exists in multiple places (reference implementations across repos), read **every** reference in full, not just the canonical one — variation across references surfaces patches before v0.1 instead of as churn later (soul#52: reading all 4 references preempted 5 of the 7 fixes a dry-run would have found). Don't substitute Explore-agent summaries for direct reads; agents sometimes report existing files as absent.
2. **Plan to files** — Write the plan into 3 files in `planning/active/`:
   - `task_plan.md` — Phases with checkbox tasks
   - `findings.md` — Research, discoveries, technical analysis
   - `progress.md` — Session log with timestamps and commit refs
3. **Plan-review with the Plan agent — concurrently, not as a gate** — Once `task_plan.md` is scaffolded, spawn the Plan subagent (`Agent({subagent_type: "Plan", prompt: "..."}`) and ask it to critically review the task_plan against the issue body + actual codebase. Categorize findings as Blocker / Gap / Ordering / Assumption / Scope / Acceptance. The agent reads files fresh — it catches what you miss when you've been thinking about the design too long. Real example: caught 21 issues including hardcoded literals across 4 files not listed in the plan, untested DB column mismatches, and a baseline-cache-shadow that would have produced a 6-second no-op run.

   **Do not wait for it.** Spawn, then start the lowest-risk phase. Background agents have repeatedly returned late — in one case after the entire issue had shipped — so treating the review as a precondition stalls the work for as long as the agent takes (see `karpathy.md` §5). Fold findings in whenever they land: pre-baseline they edit the plan; mid-implementation they become follow-up commits. A review that arrives after the code is written is not wasted — the reviewer reads real code instead of a plan, which is how one late review still contributed three fixes that no earlier reading had found. If you genuinely cannot proceed without the result, run it with `run_in_background: false` so the blocking is explicit.

   Verify before acting, in both directions. Findings have been confidently wrong (a "BLOCKER" disproved by a 30-second probe) and confidently right about things nobody suspected. Reproduce the claim first.
4. **Lock naming before the baseline** — If naming feedback surfaces during planning (legacy filename, inconsistency with an existing file family), fold the rename into the convention + task_plan BEFORE the baseline commit, not as a follow-up. Pre-baseline it's free; retrofitting after implementation cascades (soul#52: `build_exec_pdf.R` → `run_pagedown_exec_summary.R` locked in pre-baseline meant zero downstream rework).
5. **Commit the plan** — After Plan-agent review + fixes. This is the baseline.
6. **Work in atomic commits** — Each commit bundles code changes WITH checkbox updates in the planning files. The diff shows both what was done and the checkbox marking it done.
7. **Code check before commit** — Run `/code-check` on staged diffs before committing. Don't mark a task done until the diff passes review.
8. **Archive when complete** — Move `planning/active/` to `planning/archive/` via `/planning-archive`. Write a README.md in the archive directory with a one-paragraph outcome summary and closing commit/PR ref — future sessions scan these to catch up fast.

## Atomic Commits (Critical)

Every commit that completes a planned task MUST include:
- The code/script changes
- The checkbox update in `task_plan.md` (`- [ ]` -> `- [x]`)
- A progress entry in `progress.md` if meaningful

This creates a git audit trail where `git log -- planning/` tells the full story. Each commit is self-documenting — you can backtrack with git and understand everything that happened.

## File Formats

### task_plan.md

Phases with checkboxes. This is the core tracking file.

```markdown
# Task Plan

## Phase 1: [Name]
- [ ] Task description
- [ ] Another task

## Phase 2: [Name]
- [ ] Task description
```

Mark tasks done as they're completed: `- [x] Task description`

### findings.md

Append-only research log. Discoveries, technical analysis, things learned.

```markdown
# Findings

## [Topic]
[What was found, with source/date]
```

### progress.md

Session entries with commit references.

```markdown
# Progress

## Session YYYY-MM-DD
- Completed: [items]
- Commits: [refs]
- Next: [items]
```

## Directory Structure

```
planning/
  active/          <- Current work (3 PWF files)
  archive/         <- Completed issues
    YYYY-MM-issue-N-slug/
```

If `planning/` doesn't exist in the repo, run `/planning-init` first.

## Skills

| Skill | When to use |
|-------|-------------|
| `/planning-init` | First time in a repo — creates directory structure |
| `/planning-update` | Mid-session — sync checkboxes and progress |
| `/planning-archive` | Issue complete — archive and create fresh active/ |


# Reference Management Conventions

How references flow between Claude Code, Zotero, and technical writing at New Graph Environment.

## Tool Routing

Three tools, different purposes. Use the right one.

| Need | Tool | Why |
|------|------|-----|
| Search by keyword, read metadata/fulltext, semantic search | **MCP `zotero_*` tools** | pyzotero, works with Zotero item keys |
| Look up by citation key (e.g., `irvine2020ParsnipRiver`) | **`/zotero-lookup` skill** | Citation keys are a BBT feature — pyzotero can't resolve them |
| Create items, attach PDFs, deduplicate | **`/zotero-api` skill** | Connector API for writes, JS console for attachments |

**Citation keys vs item keys:** Citation keys (like `irvine2020ParsnipRiver`) come from Better BibTeX. Item keys (like `K7WALMSY`) are native Zotero. The MCP works with item keys. `/zotero-lookup` bridges citation keys to item data.

**BBT citation key storage:** As of Feb 2025+, BBT stores citation keys as a `citationKey` field directly in `zotero.sqlite` (via Zotero's item data system), not in a separate BBT database. The old `better-bibtex.sqlite` and `better-bibtex.migrated` files are stale and no longer updated. Query citation keys with: `SELECT idv.value FROM items i JOIN itemData id ON i.itemID = id.itemID JOIN itemDataValues idv ON id.valueID = idv.valueID JOIN fields f ON id.fieldID = f.fieldID WHERE f.fieldName = 'citationKey'`.

**BBT citekey format is locally patched to strip `&`:** the `citekeyFormat` pref (`extensions.zotero.translators.better-bibtex.citekeyFormat` in `~/Library/Application Support/Zotero/Profiles/*/prefs.js`) has a `.replace(find = "&", replace = "")` segment added by hand. Without it, institutional authors containing `&` (e.g. "BC Species & Ecosystem Explorer", "WA Dept of Fish & Wildlife") leak `&` into the citekey, and pandoc's `@key` parser stops at `&` — so cites render broken in any bookdown/quarto build even though biblatex accepts the key. Reapply via Zotero → Tools → Run JavaScript: `Zotero.Prefs.set("translators.better-bibtex.citekeyFormat", val)` (also patch `citekeyFormatEditing` to match). Survives Zotero/BBT auto-updates; reverts only on a profile reset or a manual edit via the BBT preferences UI. Detect drift: `grep citekeyFormat ~/Library/Application\ Support/Zotero/Profiles/*/prefs.js` should show the `.replace(find = "&", ...)` chain. Teammates on Skeena/Fraser/restoration machines that hit the same `@key`-breaks-at-`&` drift should run the same `Zotero.Prefs.set`.

## Adding References Workflow

### 1. Search and flag

When research turns up a reference:
- **DOI available:** Tell the user — Zotero's magic wand (DOI lookup) is the fastest path
- **ResearchGate link:** Flag to user for manual check — programmatic fetch is blocked (403), but full text is often there
- **BC gov report:** Search [ACAT](https://a100.gov.bc.ca/pub/acat/), for.gov.bc.ca library, EIRS viewer
- **Paywalled:** Note it, move on. Don't waste time trying to bypass.

### 2. Add to Zotero

**Preferred order:**
1. DOI magic wand in Zotero UI (fastest, most complete metadata)
2. Web API POST with `collections` array (grey literature, local PDFs — targets collection directly, no UI interaction needed)
3. `saveItems` via `/zotero-api` (batch creation from structured data — requires UI collection selection)
4. JS console script for group library (when connector can't target the right collection)

**Collection targeting:** `saveItems` drops items into whatever collection is selected in Zotero's UI. Always confirm with the user before calling it. **Web API bypasses this** — include `"collections": ["KEY"]` in the POST body. Find collection keys with `?q=name` search on the collections endpoint.

### 3. Attach PDFs

`saveItems` attachments silently fail. Don't use them. Instead:

1. **Web API S3 upload (preferred):** Create attachment item → get upload auth → build S3 body (Python: prefix + file bytes + suffix) → POST to S3 → register with uploadKey. Works without Zotero running. See `/zotero-api` skill section 4.
2. **JS console fallback:** Download with `curl`, attach via `item_attach_pdf.js` in Zotero JS console.
3. Verify attachment exists via MCP: `zotero_get_item_children`

### 4. Verify

After manual adds, confirm via MCP:
- `zotero_search_items` — find by title
- `zotero_get_item_metadata` — check fields are complete
- `zotero_get_item_children` — confirm PDF attached

### 5. Clean up

If duplicates were created (common with `saveItems` retries):
- Run `collection_dedup.js` via Zotero JS console
- It keeps the copy with the most attachments, trashes the rest

## In Reports (bookdown)

### Bibliography generation

```yaml
# index.Rmd — dynamic bib from Zotero via Better BibTeX
bibliography: "`r rbbt::bbt_write_bib('references.bib', overwrite = TRUE)`"
```

`rbbt` pulls from BBT, which syncs with Zotero. Edit references in Zotero → rebuild report → bibliography updates.

**Library targeting:** rbbt must know which Zotero library to search. This is set globally in `~/.Rprofile`:

```r
# default library — NewGraphEnvironment group (libraryID 9, group 4733734)
options(rbbt.default.library_id = 9)
```

Without this option, rbbt searches only the personal library (libraryID 1) and won't find group library references. The library IDs map to Zotero's internal numbering — use `/zotero-lookup` with `SELECT DISTINCT libraryID FROM citationkey` against the BBT database to discover available libraries.

### Citation syntax

- `[@key2020]` — parenthetical: (Author 2020)
- `@key2020` — narrative: Author (2020)
- `[@key1; @key2]` — multiple
- `nocite:` in YAML — include uncited references

### Cite primary sources

When a review paper references an older study, trace back to the original and cite it. Don't attribute findings to the review when the original exists. (See LLM Agent Conventions in `newgraph.md`.)

**When the original is unavailable** (paywalled, out of print, can't locate): use secondary citation format in the prose and include bib entries for both sources:

> Smith et al. (2003; as cited in Doctor 2022) found that...

Both `@smith2003` and `@doctor2022` go in the `.bib` file. The reader can then track down the original themselves. Flag incomplete metadata on the primary entry — it's better to have a partial reference than none at all.

## PDF Fallback Chain

When you need a PDF and the obvious URL doesn't work:

1. DOI resolver → publisher site (often has OA link)
2. Europe PMC (`europepmc.org/backend/ptpmcrender.fcgi?accid=PMC{ID}&blobtype=pdf`) — ncbi blocks curl
3. SciELO — needs `User-Agent: Mozilla/5.0` header
4. ResearchGate — flag to user for manual download
5. Semantic Scholar — sometimes has OA links
6. Ask user for institutional access

Always verify downloads: `file paper.pdf` should say "PDF document", not HTML.

## Searching Paper Content (ragnar)

### Setup (per project)
- `scripts/rag_build.R` — maps citation keys to Zotero PDF attachment keys, builds DuckDB
- `data/rag/` gitignored — store is local, not committed
- Dependencies: ragnar, Ollama with nomic-embed-text model
- See `/lit-search` skill for full recipe

### Query
`ragnar_store_connect()` then `ragnar_retrieve()` — returns chunks with source file attribution.

### Anti-patterns
- NEVER write abstracts manually — if CrossRef has no abstract, leave blank
- NEVER cite specific numbers without verifying from the source PDF via ragnar search
- NEVER paraphrase equations — copy exact notation and cite page/section
