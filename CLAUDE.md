# CLAUDE.md - STAC DEM BC Project Guidelines

## Project Overview: Automated Weekly STAC DEM BC Updates

This project implements automated weekly updates of STAC DEM BC JSONs using VM-based cron automation with incremental change detection. The implementation adopts proven performance improvements from stac_orthophoto_bc (parallel processing, pre-validation) before building automation infrastructure.

**Architecture:** VM-based cron → Change detection → Parallel validation/processing → S3 sync → PgSTAC registration

**Expected Performance:**
- First run (full): ~1-1.5 hours (down from 5-6 hours)
- Weekly runs (incremental): 5-15 minutes for typical 10-50 new files
- Cost: $0 additional (uses existing VM)

### Key Implementation Phases

**Phase 1-2: Modernization ✅ COMPLETE (phase1-2-modernization worktree)**
- Port stac_orthophoto_bc performance improvements
- Pre-validation system with COG detection
- Parallel item creation using ThreadPoolExecutor
- Incremental update logic with change detection
- Optimize spatial extent calculation
- **Result:** 100-item test passed, ready for VM automation

**Phase 3: VM Automation (phase3-automation worktree - future)**
- Master automation script (stac_update_weekly.sh)
- Cron configuration on stac-prod VM
- Benchmarking and monitoring system
- Logging infrastructure

### Project Context

**Dataset:** 58,109 DEM GeoTIFFs from BC provincial objectstore (nrs.objectstore.gov.bc.ca/gdwuts)
- Grew 158% from initial 22,548 files (discovered in Phase 2.1 change detection)
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
- ⏳ Manual execution (automation planned - Phase 3)
- ✅ Spatial extent optimized (hardcoded BC bbox)

**Goals:**
1. ~~Reduce full processing time to ~1-1.5 hours~~ → **Reality: 5-6 hours** (network I/O limited)
2. Enable weekly/monthly incremental updates (likely 30-60 min for 50-100 new files)
3. ✅ Implement robust validation and error handling
4. ⏳ Automate via VM cron jobs (Phase 3)
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
├── urls_list.txt              # Master URL list from BC objectstore (58,109 URLs)
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

### SRED Tracking
- Primary: https://github.com/NewGraphEnvironment/sred-2025-2026/issues/8
- Secondary: https://github.com/NewGraphEnvironment/sred-2025-2026/issues/3
- Repo issue: https://github.com/NewGraphEnvironment/stac_dem_bc/issues/3
- Milestone: https://github.com/NewGraphEnvironment/sred-2025-2026/milestone/1

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
- **awshak repository:** `/Users/airvine/Projects/repo/awshak`
- OpenTofu/Terraform-based infrastructure management
- S3 buckets already IaC-managed: `stac-dem-bc` (prod), can easily create `dev-stac-dem-bc` for testing
- Other managed buckets: imagery-uav-bc, stac-orthophoto-bc, water-temp-bc, backup-imagery-uav
- Features: versioning, lifecycle policies, CORS, public access controls
- Reproducible, version-controlled server setups (future)

**Note:** Phase 3 VM automation uses current manual deployment approach. S3 buckets already IaC-managed. Future phases should migrate VM provisioning to awshak for full reproducibility.

### File Locations
- **Main repo:** `/Users/airvine/Projects/repo/stac_dem_bc`
- **Phase 1-2 worktree:** `/Users/airvine/Projects/repo/stac_dem_bc-phase1-2-modernization`
- **Infrastructure repo:** `/Users/airvine/Projects/repo/awshak` (future migration)
- **Local STAC output:** `/Users/airvine/Projects/gis/stac_dem_bc/stac/prod/stac_dem_bc`
- **S3 bucket:** `s3://stac-dem-bc/`
- **VM path:** `/home/airvine/stac_dem_bc/`

<\!-- BEGIN SOUL CONVENTIONS — DO NOT EDIT BELOW THIS LINE -->

# Agent Teams Orchestration

Checklist for effectively running Claude Code agent teams. Agent teams coordinate multiple Claude Code instances working in parallel with shared tasks and inter-agent messaging.

**Source:** [code.claude.com/docs/en/agent-teams](https://code.claude.com/docs/en/agent-teams)

**Status:** Experimental. Disabled by default.

## Before You Start

- [ ] **Enable the feature flag** in `settings.json` or environment:
  ```json
  { "env": { "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1" } }
  ```
- [ ] **Choose display mode** — `in-process` (default, any terminal) or split panes (requires tmux or iTerm2). Set `teammateMode` in `settings.json` or pass `--teammate-mode`.
- [ ] **Install tmux** if using split panes (`brew install tmux`). Not supported in VS Code terminal, Windows Terminal, or Ghostty.
- [ ] **Pre-approve common permissions** in your permission settings before spawning teammates — permission prompts bubble up to the lead and create friction.

## When to Use Teams (vs. Subagents)

Use agent teams when teammates need to **talk to each other** — research debates, competing hypotheses, cross-layer coordination. Use subagents when you just need focused workers that report back results.

**Good fit:** parallel code review (security + performance + tests), investigating competing bug hypotheses, new modules that don't share files, research from multiple angles.

**Bad fit:** sequential tasks, edits to the same file, simple work where coordination overhead exceeds benefit.

## Starting a Team

- [ ] **Give enough context in the spawn prompt** — teammates don't inherit the lead's conversation history. Include file paths, architecture context, and specific focus areas.
- [ ] **Size tasks right** — too small = overhead wasted, too large = risk of divergence. Aim for self-contained units with clear deliverables. 5-6 tasks per teammate keeps everyone productive.
- [ ] **Specify models explicitly** if you want cost control (e.g., "Use Sonnet for each teammate").
- [ ] **Ensure teammates own different files** — two teammates editing the same file leads to overwrites.

## During Execution

- [ ] **Use delegate mode** (Shift+Tab) to prevent the lead from implementing tasks itself instead of waiting for teammates.
- [ ] **Monitor and steer** — check progress, redirect approaches that aren't working. Don't let a team run unattended too long.
- [ ] **Message teammates directly** — Shift+Up/Down in in-process mode, click pane in split mode. Give additional instructions or ask follow-ups.
- [ ] **If the lead starts coding instead of delegating**, tell it: "Wait for your teammates to complete their tasks before proceeding."

## Quality Gates

- [ ] **Require plan approval** for risky work — teammates plan in read-only mode until the lead approves. Give criteria: "only approve plans that include test coverage."
- [ ] **Use hooks** for automated enforcement:
  - `TeammateIdle` — exit code 2 sends feedback and keeps the teammate working
  - `TaskCompleted` — exit code 2 prevents completion and sends feedback

## Cleanup (Critical)

- [ ] **Shut down all teammates first** — ask the lead: "Ask the researcher teammate to shut down." Teammates finish their current action before stopping.
- [ ] **Then clean up the team** — tell the lead: "Clean up the team." This removes shared resources. Cleanup fails if teammates are still running.
- [ ] **Always clean up via the lead** — teammates should never run cleanup (their team context may not resolve correctly).
- [ ] **Check for orphaned tmux sessions** after cleanup:
  ```bash
  tmux ls
  tmux kill-session -t <session-name>
  ```

## Known Limitations

- **No session resumption** — `/resume` and `/rewind` don't restore in-process teammates. After resuming, tell the lead to spawn new teammates.
- **Task status can lag** — teammates sometimes fail to mark tasks complete, blocking dependents. Manually check and update if stuck.
- **One team per session** — clean up before starting a new team.
- **No nested teams** — only the lead can manage the team.
- **Lead is fixed** — can't promote a teammate or transfer leadership.
- **Permissions set at spawn** — all teammates inherit the lead's mode. Change individually after spawning if needed.

## Quick Reference

| Action | How |
|--------|-----|
| Cycle teammates | Shift+Up/Down |
| View teammate session | Enter on selected teammate |
| Interrupt teammate | Escape while viewing |
| Toggle task list | Ctrl+T |
| Enable delegate mode | Shift+Tab |
| Force in-process mode | `claude --teammate-mode in-process` |

# Bookdown Conventions

Standards for bookdown report projects across New Graph Environment.

## Template Repos

These are the canonical references. Child repos inherit their structure and patterns.

- [mybookdown-template](https://github.com/NewGraphEnvironment/mybookdown-template) — General-purpose bookdown starter
- [fish_passage_template_reporting](https://github.com/NewGraphEnvironment/fish_passage_template_reporting) — Fish passage reporting template

When in doubt, match what the template does. When the template and production repos disagree, production wins — update the template.

## Project Structure

```
project/
├── index.Rmd                # Master config, YAML params, setup chunks
├── _bookdown.yml            # book_filename, output_dir: "docs"
├── _output.yml              # Gitbook, pagedown, pdf_book config
├── 0100-intro.Rmd           # Chapter numbering: 4-digit, 100s increment
├── 0200-background.Rmd
├── 0300-methods.Rmd
├── 0400-results.Rmd
├── 0500-*.Rmd               # Discussion/recommendations
├── 0800-appendix-*.Rmd      # Appendices (site-specific in fish passage)
├── 2000-references.Rmd      # Auto-generated from .bib
├── 2090-report-change-log.Rmd  # Auto-generated from NEWS.md
├── 2100-session-info.Rmd    # Reproducibility
├── NEWS.md                  # Changelog (semantic versioning)
├── scripts/
│   ├── packages.R           # Package loading (renv-managed)
│   ├── functions.R          # Project-specific functions
│   ├── staticimports.R      # Auto-generated from staticimports pkg
│   ├── setup_docs.R         # Build helper
│   └── run.R                # Local build (gitbook + PDF)
├── fig/                     # Figures (organized by chapter or type)
├── data/                    # Project data
├── docs/                    # Rendered output (GitHub Pages)
├── renv.lock                # Locked dependencies
└── .Rprofile                # Activates renv
```

## Setup Chunk Pattern

Every `index.Rmd` follows this setup sequence. Order matters.

```r
# 1. Gitbook vs PDF switch
gitbook_on <- TRUE

# 2. Knitr options
knitr::opts_chunk$set(
  echo = identical(gitbook_on, TRUE),  # Show code only in gitbook
  message = FALSE, warning = FALSE,
  dpi = 60, out.width = "100%"
)
options(scipen = 999)
options(knitr.kable.NA = '--')
options(knitr.kable.NAN = '--')

# 3. Source in order: packages → static imports → functions → data
source('scripts/packages.R')
source('scripts/staticimports.R')
source('scripts/functions.R')
```

Responsive settings by output format:

```r
# Gitbook
photo_width <- "100%"; font_set <- 11

# PDF (paged.js)
photo_width <- "80%"; font_set <- 9
```

## YAML Parameters

Parameters live in `index.Rmd` frontmatter (not a separate file). Child repos override by editing these values.

```yaml
params:
  repo_url: 'https://github.com/NewGraphEnvironment/repo_name'
  report_url: 'https://www.newgraphenvironment.com/repo_name/'
  update_packages: FALSE
  update_bib: TRUE
  gitbook_on: TRUE
```

Fish passage repos add project-specific params (`project_region`, `model_species`, `wsg_code`, update flags for forms). These are project-specific — don't add them to the general template.

## Chunk Naming

Embed context and purpose in chunk names. The principle is universal; the codes are project-specific.

**Pattern:** `{type}-{system}-{description}`

| Type | Examples |
|------|---------|
| Tables | `tab-kln-load-int-yr`, `tab-sites-sum`, `tab-wshd-196332` |
| Figures | `plot-wq-kln-quadratic`, `map-interactive`, `map-196332` |
| Photos | `photo-196332-01`, `photo-196332-d01` (dual layout) |

## Cross-References

Bookdown auto-prepends `fig:` or `tab:` to chunk names.

- **Tables:** `Table \@ref(tab:chunk-name)`
- **Figures:** `Figure \@ref(fig:chunk-name)`

No `fig:` or `tab:` prefix in the chunk label itself — bookdown adds it.

## Table Caption Workaround

Interactive tables (DT) can't use standard bookdown captions. Use the `my_tab_caption()` function from `staticimports.R`.

**Pattern:** Separate `-cap` chunk from table chunk.

```r
# Caption chunk — must use results="asis"
{r tab-sites-sum-cap, results="asis"}
my_caption <- "Summary of fish passage assessment procedures."
my_tab_caption()
```

```r
# Table chunk — renders the DT
{r tab-sites-sum}
data |> my_dt_table(page_length = 20, cols_freeze_left = 0)
```

`my_tab_caption()` auto-grabs the chunk label via `knitr::opts_current$get()$label` and wraps it in HTML caption tags that bookdown can cross-reference.

## Photo Layout

Separate prep chunk (find the file) from display chunk (render it).

```r
# Prep — find the photo
{r photo-196332-01-prep}
my_photo1 <- fpr::fpr_photo_pull_by_str(str_to_pull = 'ds_typical_1_')
my_caption1 <- paste0('Typical habitat downstream of PSCIS crossing ', my_site, '.')
```

```r
# Gitbook — full width
{r photo-196332-01, fig.cap=my_caption1, out.width=photo_width, eval=gitbook_on}
knitr::include_graphics(my_photo1)
```

```r
# PDF — side by side with 1% spacer
{r photo-196332-d01, fig.show="hold", out.width=c("49.5%","1%","49.5%"), eval=identical(gitbook_on, FALSE)}
knitr::include_graphics(my_photo1)
knitr::include_graphics("fig/pixel.png")
knitr::include_graphics(my_photo2)
```

## Bibliography

Use `rbbt` (Better BibTeX) to dynamically generate `references.bib` from Zotero.

```yaml
bibliography: "`r rbbt::bbt_write_bib('references.bib', overwrite = TRUE)`"
biblio-style: apalike
link-citations: no
```

Auto-generate package citations:

```r
knitr::write_bib(c(.packages(), 'bookdown', 'knitr', 'rmarkdown'), 'packages.bib')
```

Use `nocite:` in YAML to include references not cited in text.

## Conditional Rendering (Gitbook vs PDF)

A single boolean `gitbook_on` controls output format throughout.

```r
# Show only in gitbook
{r map-interactive, eval=gitbook_on}

# Show only in PDF
{r fig-print-only, eval=identical(gitbook_on, FALSE)}

# Conditional inline content
`r if(identical(gitbook_on, FALSE)) knitr::asis_output("This report is available online...")`

# Page breaks for PDF only
`r if(gitbook_on){knitr::asis_output("")} else knitr::asis_output("\\pagebreak")`
```

## Versioning and Changelog

Reports use MAJOR.MINOR.PATCH versioning with a `NEWS.md` changelog.

**Version in `index.Rmd` YAML:**
```yaml
date: |
 |
 | Version 1.1.0 DRAFT `r format(Sys.Date(), "%Y-%m-%d")`
```

**NEWS.md format:**
```markdown
## 1.1.0 (2026-02-17)

- Add feature X
- Fix issue Y ([Issue #N](https://github.com/Org/repo/issues/N))
```

**Auto-append as appendix** via `my_news_to_appendix()` in `staticimports.R`:
```r
news_to_appendix(md_name = "NEWS.md", rmd_name = "2090-report-change-log.Rmd")
```

**Convention:**
- Bump version in `index.Rmd` and add NEWS entry for every commit to main that changes report content
- Tag releases: `git tag -a v1.1.0 -m "v1.1.0: Brief description"`
- MAJOR: structural changes, new chapters, methodology changes
- MINOR: new content, figures, tables, discussion sections
- PATCH: prose fixes, corrections, formatting

## COG Viewer Embedding

Always use `ngr::ngr_str_viewer_cog()` — never hardcode viewer iframes.

```r
knitr::asis_output(ngr::ngr_str_viewer_cog("https://bucket.s3.us-west-2.amazonaws.com/ortho.tif"))
```

The function includes a cache-busting `?v=` parameter. Bump `v` in the function default when `viewer.html` has breaking changes.

## Dependency Management

Use `renv` for reproducible package management:
- `.Rprofile` activates renv on startup
- `renv::restore()` installs from lockfile
- `renv::snapshot()` updates lockfile after adding packages
- Use `pak::pak("pkg")` to install (not `install.packages`)

## Known Drift

Production repos (2024-2025) have drifted from templates in these areas. When working in a child repo, match what that repo does, not the template:

- **Script naming in `02_reporting/`** — older repos use `tables.R`, `0165-read-sqlite.R`; newer repos use numbered `0130-tables.R`. Follow the repo you're in.
- **Removed packages** — `elevatr`, `rayshader`, `arrow` removed from production but still in template.
- **`staticimports::import()` call** — some repos skip it and source `staticimports.R` directly.
- **Hardcoded vs parameterized years** — older repos hardcode years in file paths; newer repos use `params$project_year`. Prefer parameterized.

# Communications Conventions

Standards for external communications across New Graph Environment.

[compost](https://github.com/NewGraphEnvironment/compost) is the working repo for email drafts, scripts, contact management, and Gmail utilities. These conventions capture the universal principles; compost has the implementation details.

## Tone

Three levels. Default to casual unless context dictates otherwise.

| Level | When | Style |
|-------|------|-------|
| **Casual** | Established working relationships | Professional but warm. Direct, concise. No slang. |
| **Very casual** | Close collaborators with rapport | Colloquial OK. Light humor. Slang acceptable. |
| **Formal** | New contacts, senior officials, formal requests | Full sentences, no contractions, state purpose early. |

**Collaborative, not directive.** Acknowledge their constraints:

- **Avoid:** "Work these in as makes sense for your lab"
- **Better:** "If you're able to work these in when it fits your schedule that would be really helpful"

## Email Workflow

Draft in markdown, convert to HTML at send time via gmailr. See compost for script templates, OAuth setup, and `search_gmail.R`.

**File naming:** `YYYYMMDD_recipient_topic_draft.md` + `YYYYMMDD_recipient_topic.R`

**Key gotchas** (documented in detail in compost):
- Gmail strips `<style>` blocks — use inline styles for tables
- `gm_create_draft()` does NOT support `thread_id` — only `gm_send_message()` can reply into threads. Drafts land outside the conversation.
- Always use `test_mode` and `create_draft` variables for safe workflows

## Data in Emails

- **Never manually type data into tables** — generate programmatically from source files
- **Link to canonical sources** (GitHub repos, public reports) rather than embedding raw data
- **Provide both CSV and Excel** when sharing tabular data
- **Document ID codes** — when using compressed IDs (e.g., `id_lab`), include a reference sheet so recipients can decode

## What Not to Expose Externally

- Internal QA info (blanks, control samples, calibration data)
- Internal tracking codes or SRED references
- Draft status or revision history
- Internal project management details

Keep client-facing communications focused on deliverables and technical content.

## Signature

```
Al Irvine B.Sc., R.P.Bio.
New Graph Environment Ltd.

Cell: 250-777-1518
Email: al@newgraphenvironment.com
Website: www.newgraphenvironment.com
```

In HTML emails, use `<br>` tags between lines.

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

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

# New Graph Environment Conventions

Core patterns for professional, efficient workflows across New Graph Environment repositories.

## Ecosystem Overview

Four repos form the governance and operations layer across all New Graph Environment work:

| Repo | Purpose | Analogy |
|------|---------|---------|
| [compass](https://github.com/NewGraphEnvironment/compass) | Ethics, values, guiding principles | The "why" |
| [soul](https://github.com/NewGraphEnvironment/soul) | Standards, skills, conventions for LLM agents | The "how" |
| [compost](https://github.com/NewGraphEnvironment/compost) | Communications templates, email workflows, contact management | The "who" |
| [awshak](https://github.com/NewGraphEnvironment/awshak) | Infrastructure as Code, deployment | The "where" |

**Adaptive management:** Conventions evolve from real project work, not theory. When a pattern is learned or refined during project work, propagate it back to soul so all projects benefit. The `/claude-md-init` skill builds each project's `CLAUDE.md` from soul conventions.

**Cross-references:** [sred-2025-2026](https://github.com/NewGraphEnvironment/sred-2025-2026) tracks R&D activities across repos. Compost cross-cuts all projects as the centralized communications workflow — email drafts, contact registry, and tone guidelines live there and are copied to individual project `communications/` folders as needed.

## Issue Workflow

### Before Creating an Issue (non-negotiable)

1. **Check for duplicates:** `gh issue list --state open --search "<keywords>"` -- search before creating
2. **Link to SRED:** If work involves infrastructure, R&D, tooling, or performance benchmarking, add `Relates to NewGraphEnvironment/sred-2025-2026#N` (match by repo name in SRED issue title)
3. **One issue, one concern.** Keep focused.

### Professional Issue Writing

Write issues with clear technical focus:

- **Use normal technical language** in titles and descriptions
- **Focus on the problem and solution** approach
- **Add tracking links at the end** (e.g., `Relates to Owner/repo#N`)

**Issue body structure:**
```markdown
## Problem
<what's wrong or missing>

## Proposed Solution
<approach>

Relates to #<local>
Relates to NewGraphEnvironment/sred-2025-2026#<N>
```

### GitHub Issue Creation - Always Use Files

The `gh issue create` command with heredoc syntax fails repeatedly with EOF errors. ALWAYS use `--body-file`:

```bash
cat > /tmp/issue_body.md << 'EOF'
## Problem
...

## Proposed Solution
...
EOF

gh issue create --title "Brief technical title" --body-file /tmp/issue_body.md
```

## Closing Issues

**DO:** Close issues via commit messages. The commit IS the closure and the documentation.

```
Fix broken DEM path in loading pipeline

Update hardcoded path to use config-driven resolution.

Fixes #20
Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
```

**DON'T:** Close issues with `gh issue close`. This breaks the audit trail — there's no linked diff showing what changed.

- `Fixes #N` or `Closes #N` — auto-closes and links the commit to the issue
- `Relates to #N` — partial progress, does not close
- Always close issues when work is complete. Don't leave stale open issues.

## Commit Quality

Write clear, informative commit messages:

```
Brief description (50 chars or less)

Detailed explanation of changes and impact.

Fixes #<issue> (or Relates to #<issue>)
Relates to NewGraphEnvironment/sred-2025-2026#<N>

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
```

**When to commit:**
- Logical, atomic units of work
- Working state (tests pass)
- Clear description of changes

**What to avoid:**
- "WIP" or "temp" commits in main branch
- Combining unrelated changes
- Vague messages like "fixes" or "updates"

## LLM Agent Conventions

Rules learned from real project sessions. These apply across all repos.

- **Cite primary sources** — when a review paper references an older study, trace back to the original and cite it. Don't attribute findings to the review when the original exists.
- **Install missing packages, don't workaround** — if a package is needed, ask the user to install it (e.g. `pak::pak("pkg")`). Don't write degraded fallback code to avoid the dependency.
- **Never hardcode extractable data** — if coordinates, station names, or metadata can be pulled from an API or database at runtime, do that. Don't hardcode values that have a programmatic source.
- **Close issues via commits, not `gh issue close`** — see Closing Issues above.

## Naming Conventions

**Pattern: `noun_verb-detail`** -- noun first, verb second across all naming:

| What | Example |
|------|---------|
| Skills | `claude-md-init`, `gh-issue-create`, `planning-update` |
| Scripts | `stac_register-baseline.sh`, `stac_register-pypgstac.sh` |
| Logs | `20260209_stac_register-baseline_stac-dem-bc.txt` |
| Log format | `yyyymmdd_noun_verb-detail_target.ext` |

Scripts and logs live together: `scripts/<module>/logs/`

## Projects vs Milestones

- **Projects** = daily cross-repo tracking (always add to relevant project)
- **Milestones** = iteration boundaries (only for release/claim prep)
- Don't double-track unless there's a reason

| Content | Project |
|---------|---------|
| R&D, experiments, SRED-related | **SRED R&D Tracking (#8)** |
| Data storage, sqlite, postgres, pipelines | **Data Architecture (#9)** |
| Fish passage field/reporting | **Fish Passage 2025 (#6)** |
| Restoration planning | **Aquatic Restoration Planning (#5)** |
| QGIS, Mergin, field forms | **Collaborative GIS (#3)** |

# PR Review Automation

Automated PR review and interactive Claude mentions using Claude Code GitHub Action.

## Setup (per repo)

1. **Install the Claude Code GitHub App** via `/install-github-app`
   - Sets `CLAUDE_CODE_OAUTH_TOKEN` as a repo secret automatically
   - Uses Max subscription (flat $200/mo, not per-token)

2. **Enable the workflow** via `/claude-review-enable`
   - Choose mode: **review** (auto PR review + @claude mentions) or **mention** (@claude only)
   - Pushes the workflow template to `.github/workflows/claude.yml`

3. **Ensure CLAUDE.md exists** in the repo — the action reads it automatically.
   Use `/claude-md-init` to set up if missing.

## What It Does

### Automatic PR Review (Haiku)
On every PR open/update, Claude reviews the diff and posts:
- **Inline comments** on specific lines with issues
- **Summary comment** with overall assessment
- Uses Haiku for speed and cost (~$0.01-0.05 per review)

### Interactive @claude Mentions (Sonnet)
Comment `@claude <request>` on any issue or PR. Claude can:
- Answer questions about the codebase
- Create branches and open PRs from issue descriptions
- Fix code and push commits
- Run slash commands: `@claude /review`
- Uses Sonnet for deeper reasoning

## Model Selection

| Job | Model | Why |
|-----|-------|-----|
| Auto-review | Haiku 4.5 | Fast, cheap, good enough for style/lint/bug checks |
| Interactive | Sonnet 4.5 | Needs reasoning for code changes, PR creation |

To override, edit `claude_args` in the workflow:
```yaml
claude_args: |
  --model claude-sonnet-4-5-20250929
```

Available models (use exact IDs):
- `claude-haiku-4-5-20251001` — cheapest, fastest
- `claude-sonnet-4-5-20250929` — balanced
- `claude-opus-4-6` — most capable, expensive

## Interactive Examples

```
@claude what does the validate_crossing function do?
@claude fix the failing lintr checks and open a PR
@claude /review — focus on the database query performance
@claude add tests for the new helper functions in R/utils.R
```

## Bot Workflow (for OpenClaw bots opening PRs)

When a bot opens a PR, it should:

1. **Push the PR** and wait for the Claude review action to complete
2. **Read review comments** via `gh pr view --comments`
3. **Fix no-brainers** automatically: lint issues, typos, missing imports
4. **Ask humans** for judgment calls: architecture, logic changes, scope
5. **Push fix commits** to re-trigger the review
6. **Stop after 3 rounds** — if still failing, tag a human reviewer

## Cost

| Model | Input | Output | Typical review |
|-------|-------|--------|----------------|
| Haiku 4.5 | $1/M | $5/M | ~$0.01-0.05 |
| Sonnet 4.5 | $3/M | $15/M | ~$0.05-0.25 |
| Opus 4.6 | $15/M | $75/M | ~$0.25-1.00 |

Use `--max-turns` to cap iterations and cost.

## Reference

- [claude-code-action](https://github.com/anthropics/claude-code-action)
- [Setup guide](https://github.com/anthropics/claude-code-action/blob/main/docs/setup.md)

# R Package Development Conventions

Standards for R package development across New Graph Environment repositories.
Based on [R Packages (2e)](https://r-pkgs.org/) by Hadley Wickham and Jenny Bryan.

## Style

- tidyverse style guide: snake_case, pipe operators (`|>` or `%>%`)
- Match existing patterns in each codebase
- Use `pak` for package installation (not `install.packages`)

## Package Structure

Follow R Packages (2e) conventions:
- `R/` for functions, `tests/testthat/` for tests, `man/` for docs
- `DESCRIPTION` with proper fields (Title, Description, Authors@R)
- `NAMESPACE` managed by roxygen2 (`#' @export`, `#' @importFrom`)
- Never edit `NAMESPACE` or `man/` by hand

## Testing

- Use testthat 3e (`Config/testthat/edition: 3` in DESCRIPTION)
- Run `devtools::test()` before committing
- Test files mirror source: `R/utils.R` -> `tests/testthat/test-utils.R`
- Use `testthat::snapshot_test()` for complex outputs

## lintr

Run `lintr::lint_package()` before committing R package code. Fix all warnings — every lint should be worth fixing.

### Recommended .lintr config

```r
linters: linters_with_defaults(
    line_length_linter(120),
    object_name_linter(styles = c("snake_case", "dotted.case")),
    commented_code_linter = NULL
  )
exclusions: list(
    "renv" = list(linters = "all")
  )
```

- 120 char line length (default 80 is too strict for data pipelines)
- Allow dotted.case (common in base R and legacy code)
- Suppress commented code lints (exploratory R scripts often have commented alternatives)
- Exclude renv directory entirely

## Documentation

- roxygen2 for all exported functions
- `@examples` for non-trivial functions
- Vignettes for workflows, not just API reference
- `pkgdown` site if the package is public

## Dependencies

- Minimize Imports — use `Suggests` for packages only needed in tests/vignettes
- Pin versions only when breaking changes are known
- Prefer packages already in the tidyverse ecosystem

## LLM Workflow

When an LLM assistant modifies R package code:
1. Run `lintr::lint_package()` — fix issues before committing
2. Run `devtools::test()` — ensure tests pass
3. Run `devtools::document()` if roxygen comments changed
4. Check `devtools::check()` passes for releases

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

**BBT database location:** BBT migrated from `better-bibtex.sqlite` to `better-bibtex.migrated` (Feb 2025+). The old `.sqlite` file is stale — always use `better-bibtex.migrated` for citation key lookups. The `better-bibtex-search.sqlite` is also stale and unrelated.

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
2. `saveItems` via `/zotero-api` (good for batch creation from structured data)
3. JS console script for group library (when connector can't target the right collection)

**Collection targeting:** `saveItems` drops items into whatever collection is selected in Zotero's UI. Always confirm with the user before calling it.

### 3. Attach PDFs

`saveItems` attachments silently fail. Don't use them. Instead:

1. Download with `curl` (see `/zotero-api` skill for source-specific patterns)
2. Attach via `item_attach_pdf.js` in Zotero JS console
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

# SRED Conventions

How SR&ED (Scientific Research and Experimental Development) tracking integrates with New Graph Environment's development workflows.

## The Claim: One Project

All SRED-eligible work across NGE falls under a **single continuous project**:

> **Dynamic GIS-based Data Processing and Reporting Framework**

- **Field:** Software Engineering (2.02.09)
- **Start date:** May 2022
- **Status:** Ongoing
- **Fiscal year:** May 1 – April 30
- **Consultant:** Boast Capital (prepares final technical report)

**Do not fragment work into separate claims.** Each fiscal year's work is structured as iterations within this one project. Internal tracking (experiment numbers in `sred-2025-2026`) maps to iterations — Boast assembles the final narrative.

## What We're Building

The SRED investment is in the **system**, not the reports it produces. Reports are service revenue. The framework is the IP and the competitive asset.

The system is an integrated, version-controlled framework that:
- Generates GIS projects from parameterized inputs
- Synchronizes field data collection with cloud-hosted databases and portable GeoPackages
- Processes and catalogs UAV imagery via STAC pipelines
- Produces dynamic, reproducible reports (bookdown/Quarto)
- Orchestrates workflows via LLM agents with shared conventions and skills
- Manages communications, time tracking, and evidence as part of the framework

No other environmental consultancy has this level of integration. This is the international competitiveness that SRED exists to incentivize.

## System Components

| Layer | Repos | Role in Framework |
|-------|-------|-------------------|
| **Agent governance** | soul | Conventions, skills, settings — how agents behave across all repos |
| **Communications** | compost | Centralized email workflows, contact management, tone standards |
| **Operations** | rolex | Time tracking, invoicing, SRED evidence, budget management |
| **Infrastructure** | awshak | IaC (OpenTofu), S3, IAM, CORS, OIDC — reproducible cloud environments |
| **GIS automation** | rfp, ngr, dff-2022 | QGIS project generation, spatial data processing, layer management |
| **Imagery** | stac_uav, stac_orthophoto_bc | UAV processing, STAC cataloging, containerized pipelines |
| **Citations** | xciter | Pandoc hooks for citations in interactive tables |
| **Data** | db_newgraph, bcfishpass, fwapg | PostgreSQL spatial databases, modelling, query APIs |
| **Field collection** | Mergin Maps projects | Mobile forms, offline GeoPackages, bidirectional sync |
| **Reporting** | Annual project repos | Bookdown reports consuming all of the above |

## Fiscal Year Iterations

Each fiscal year, Boast structures the claim as iterations within the project. Our internal experiment numbers map to these.

### FY2024 (May 2023 – April 2024)

Single narrative: version control for GIS projects + dynamic reporting engine.

### FY2025 (May 2024 – April 2025)

| Iteration | Focus | Key Repos |
|-----------|-------|-----------|
| 1 | GIS–RMarkdown synchronization | rfp, ngr, dff-2022 |
| 2 | UAV imagery + STAC cataloging | stac_uav, stac_orthophoto_bc |
| 3 | Citation handling (xciter) | xciter |
| 4 | Field-to-cloud data workflows | Mergin, PostgreSQL, GeoPackages |
| 5 | Infrastructure as Code | awshak |

### FY2026 (May 2025 – April 2026)

| Iteration | Focus | Key Repos |
|-----------|-------|-----------|
| TBD | LLM agent orchestration & governance | soul, agent teams |
| TBD | Communications workflow | compost |
| TBD | Time tracking & evidence management | rolex |
| TBD | MCP integrations (Zotero, PostgreSQL, Harvest, Xero) | soul settings, MCP configs |
| TBD | Continued GIS/reporting improvements | rfp, ngr, bookdown repos |

Iteration numbers are assigned by Boast in the final report. We provide evidence organized by theme.

## Tagging Work for SRED

### Commits

Use `Relates to NewGraphEnvironment/sred-2025-2026#N` in commit messages when work is SRED-eligible. The `/sred-commit` skill handles this.

### Time entries (rolex)

Tag hours with `sred_experiment` field linking to the relevant `sred-2025-2026` issue number. This enables automated evidence reports.

### GitHub issues

Link SRED-eligible issues to the tracking repo: `Relates to NewGraphEnvironment/sred-2025-2026#N`

## What Qualifies as SRED

From the Boast interview guide and CRA guidelines:

**Eligible (systematic investigation to overcome technological uncertainty):**
- Building tools/functions that don't exist in standard practice
- Prototyping new integrations between systems (GIS ↔ reporting ↔ field collection)
- Testing whether an approach works and documenting why it did/didn't
- Iterating on failed approaches with new hypotheses

**Not eligible (but still important to document as due diligence):**
- Evaluating existing tools to confirm they can't meet requirements
- Standard configuration of known tools
- Routine bug fixes in working systems
- Writing reports using the framework (that's service delivery)

**The test:** "Did we try something we weren't sure would work, and did we learn something from the attempt?" If yes, it's likely eligible.

## SRED and Contractors

Subcontractors are eligible for SRED at a reduced rate (~32% vs ~64% for T4 employees). Arm's length, Canadian contractors only. The work must be performed in Canada.

For core SRED work, T4 employees generate roughly 2x the credit per dollar spent. Use contractors when needed but be aware of the credit differential.

## Evidence for Boast

At fiscal year-end, Boast needs:
1. **Time records** — hours by person, tagged to iterations (from rolex)
2. **Technical narrative** — what was attempted, what worked, what didn't (from commit history, issues, PRs)
3. **Financial summary** — wages, contractor payments, expenses (from rolex + Xero)
4. **GitHub evidence** — commits, issues, PRs showing systematic investigation (from `sred-2025-2026` cross-references)

The better our tagging during the year, the less work at claim time.

## Fiscal Calendar

| Period | Dates |
|--------|-------|
| FY2024 | May 1 2022 – April 30 2024 |
| FY2025 | May 1 2024 – April 30 2025 |
| FY2026 | May 1 2025 – April 30 2026 |

Claims are filed within 18 months of fiscal year-end.
