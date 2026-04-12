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


# Code Check Conventions

Structured checklist for reviewing diffs before commit. Used by `/code-check`.
Add new checks here when a bug class is discovered — they compound over time.

## Shell Scripts

### Quoting
- Variables in double-quoted strings containing single quotes break if value has `'`
- `"echo '${VAR}'"` — if VAR contains `'`, shell syntax breaks
- Use `printf '%s\n' "$VAR" | command` to pipe values safely
- Heredocs: unquoted `<<EOF` expands variables locally, `<<'EOF'` does not — know which you need

### Paths
- Hardcoded absolute paths (`/Users/airvine/...`) break for other users
- Use `REPO_ROOT="$(cd "$(dirname "$0")/<relative>" && pwd)"`
- After moving scripts, verify `../` depth still resolves correctly
- Usage comments should match actual script location

### Silent Failures
- `|| true` hides real errors — is the failure actually safe to ignore?
- Empty variable before destructive operation (rm, destroy) — add guard: `[ -n "$VAR" ] || exit 1`
- `grep` returning empty silently — downstream commands get empty input

### Process Visibility
- Secrets passed as command-line args are visible in `ps aux`
- Use env files, stdin pipes, or temp files with `chmod 600` instead

## Cloud-Init (YAML)

### ASCII
- Must be pure ASCII — em dashes, curly quotes, arrows cause silent parse failure
- Check with: `perl -ne 'print "$.: $_" if /[^\x00-\x7F]/' file.yaml`

### State
- `cloud-init clean` causes full re-provisioning on next boot — almost never what you want before snapshot
- Use `tailscale logout` not `tailscale down` before snapshot (deregister vs disconnect)

### Template Variables
- Secrets rendered via `templatefile()` are readable at `169.254.169.254` metadata endpoint
- Acceptable for ephemeral machines, document the tradeoff

## OpenTofu / Terraform

### State
- Parsing `tofu state show` text output is fragile — use `tofu output` instead
- Missing outputs that scripts need — add them to main.tf
- Snapshot/image IDs in tfvars after deleting the snapshot — stale reference

### Destructive Operations
- Validate resource IDs before destroy: `[ -n "$ID" ] || exit 1`
- `tofu destroy` without `-target` destroys everything including reserved IPs
- Snapshot ID extraction: use `--resource droplet` and `grep -F` for exact match

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

## R / Package Installation

### pak Behavior
- pak stops on first unresolvable package — all subsequent packages are skipped
- Removed CRAN packages (like `leaflet.extras`) must move to GitHub source
- PPPM binaries may lag a few hours behind new CRAN releases

### Reproducibility
- Branch pins (`pkg@branch`) are not reproducible — document why used
- Pinned download URLs (RStudio .deb) go stale — document where to update

## General

### Documentation Staleness
- Moving/renaming scripts: update CLAUDE.md, READMEs, usage comments
- New variables: update .tfvars.example
- New workflows: update relevant README


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


**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.


# New Graph Environment Conventions

Core patterns for professional, efficient workflows across New Graph Environment repositories.

## Ecosystem Overview

Five repos form the governance and operations layer across all New Graph Environment work:

| Repo | Purpose | Analogy |
|------|---------|---------|
| [compass](https://github.com/NewGraphEnvironment/compass) | Ethics, values, guiding principles | The "why" |
| [soul](https://github.com/NewGraphEnvironment/soul) | Standards, skills, conventions for LLM agents | The "how" |
| [compost](https://github.com/NewGraphEnvironment/compost) | Communications templates, email workflows, contact management | The "who" |
| [rtj](https://github.com/NewGraphEnvironment/rtj) (formerly awshak) | Infrastructure as Code, deployment | The "where" |
| [gq](https://github.com/NewGraphEnvironment/gq) | Cartographic style management across QGIS, tmap, leaflet, web | The "look" |

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

- **Install missing packages, don't workaround** — if a package is needed, ask the user to install it (e.g. `pak::pak("pkg")`). Don't write degraded fallback code to avoid the dependency.
- **Never hardcode extractable data** — if coordinates, station names, or metadata can be pulled from an API or database at runtime, do that. Don't hardcode values that have a programmatic source.
- **Close issues via commits, not `gh issue close`** — see Closing Issues above.
- **Cite primary sources** — see references conventions.

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


# Planning Conventions

How Claude manages structured planning for complex tasks using planning-with-files (PWF).

## When to Plan

Use PWF when a task has multiple phases, requires research, or involves more than ~5 tool calls. Triggers:
- User says "let's plan this", "plan mode", "use planning", or invokes `/planning-init`
- Complex issue work begins (multi-step, uncertain approach)
- Claude judges the task warrants structured tracking

Skip planning for single-file edits, quick fixes, or tasks with obvious next steps.

## The Workflow

1. **Explore first** — Enter plan mode (read-only). Read code, trace paths, understand the problem before proposing anything.
2. **Plan to files** — Write the plan into 3 files in `planning/active/`:
   - `task_plan.md` — Phases with checkbox tasks
   - `findings.md` — Research, discoveries, technical analysis
   - `progress.md` — Session log with timestamps and commit refs
3. **Commit the plan** — Commit the planning files before starting implementation. This is the baseline.
4. **Work in atomic commits** — Each commit bundles code changes WITH checkbox updates in the planning files. The diff shows both what was done and the checkbox marking it done.
5. **Code check before commit** — Run `/code-check` on staged diffs before committing. Don't mark a task done until the diff passes review.
6. **Archive when complete** — Move `planning/active/` to `planning/archive/` via `/planning-archive`. Write a README.md in the archive directory with a one-paragraph outcome summary and closing commit/PR ref — future sessions scan these to catch up fast.

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


# SRED Conventions

How SR&ED tracking integrates with New Graph Environment's development workflows.

## The Claim: One Project

All SRED-eligible work across NGE falls under a **single continuous project**:

> **Dynamic GIS-based Data Processing and Reporting Framework**

- **Field:** Software Engineering (2.02.09)
- **Start date:** May 2022
- **Fiscal year:** May 1 – April 30
- **Consultant:** Boast Capital (prepares final technical report)

**Do not fragment work into separate claims.** Each fiscal year's work is structured as iterations within this one project. Internal tracking (experiment numbers in `sred-2025-2026`) maps to iterations — Boast assembles the final narrative.

## Tagging Work for SRED

### Commits

Use `Relates to NewGraphEnvironment/sred-2025-2026#N` in commit messages when work is SRED-eligible.

### Time entries (rolex)

Tag hours with `sred_ref` field linking to the relevant `sred-2025-2026` issue number.

### GitHub issues

Link SRED-eligible issues to the tracking repo: `Relates to NewGraphEnvironment/sred-2025-2026#N`

## What Qualifies as SRED

**Eligible (systematic investigation to overcome technological uncertainty):**
- Building tools/functions that don't exist in standard practice
- Prototyping new integrations between systems (GIS ↔ reporting ↔ field collection)
- Testing whether an approach works and documenting why it did/didn't
- Iterating on failed approaches with new hypotheses

**Not eligible:**
- Standard configuration of known tools
- Routine bug fixes in working systems
- Writing reports using the framework (that's service delivery)

**The test:** "Did we try something we weren't sure would work, and did we learn something from the attempt?" If yes, it's likely eligible.
