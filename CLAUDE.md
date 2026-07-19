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
- Primary: https://github.com/NewGraphEnvironment/sred/issues/8 — PR-body xref form: `Relates to NewGraphEnvironment/sred#8`
- (The old `sred-2025-2026` repo refs are superseded — SRED tracking lives in the `sred` repo)

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
- **Infrastructure repo:** `/Users/airvine/Projects/repo/rtj` (formerly awshak; provisions the bucket, OIDC role, and geoserv STAC host)
- **STAC catalog:** `s3://stac-dem-bc/` is the only complete copy. Local builds write to a scratch workspace via the `STAC_OUTPUT_DIR` env override in `scripts/stac_utils.py` (the old `/Users/airvine/Projects/gis/.../stac/prod` dir is empty/historical)

<\!-- BEGIN SOUL CONVENTIONS — DO NOT EDIT BELOW THIS LINE -->


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

### Quoting
- Variables in double-quoted strings containing single quotes break if value has `'`
- `"echo '${VAR}'"` — if VAR contains `'`, shell syntax breaks
- Use `printf '%s\n' "$VAR" | command` to pipe values safely
- Heredocs: unquoted `<<EOF` expands variables locally, `<<'EOF'` does not — know which you need
- Pass-through-ssh args: `printf '%q'` escapes per-arg so workload paths with spaces / quotes / metacharacters survive the local-shell → ssh-argv → remote-shell round-trip. Without it, `ssh host 'cmd' "$path"` joins args with spaces on remote and re-parses, losing argument boundaries.
- `git commit -m "$(cat <<'EOF' ... EOF)"` chokes on apostrophes in prose bodies in some contexts — the bash parser surfaces an unmatched-quote error even though heredoc bodies should be quote-neutral. Resilient default for multi-line commit messages: write the body to `/tmp/msg.txt` and use `git commit -F /tmp/msg.txt`.

### Heredoc precedence in pipelines
- `cmd1 | cmd2 <<EOF` — the heredoc binds to `cmd2` (the rightmost simple command). If you intended `cmd1` to receive it, put `<<EOF` on cmd1 explicitly: `cmd1 <<EOF | cmd2`.
- Symptom when wrong: ssh body silently echoed by tee/cat/etc, ssh side gets empty stdin, exits 0 (or near-0) without doing anything. Caught the hard way 2026-05-01 in cypher_restore-fwapg.sh.

### pipefail with ssh+tee
- `set -eu` does NOT propagate exit codes through pipelines. `ssh ... | tee log` returns tee's exit (always 0 for healthy tee), masking ssh failure.
- Use `set -euo pipefail` for any script that pipes a meaningful command into tee/cat/grep/etc. Or check `${PIPESTATUS[0]}` explicitly.
- Symptom when wrong: task notifications report "exit 0 / completed" while remote work was actually skipped or errored.

### Paths
- Hardcoded absolute paths (`/Users/airvine/...`) break for other users
- Use `REPO_ROOT="$(cd "$(dirname "$0")/<relative>" && pwd)"`
- After moving scripts, verify `../` depth still resolves correctly
- Usage comments should match actual script location

### Silent Failures
- `|| true` hides real errors — is the failure actually safe to ignore?
- Empty variable before destructive operation (rm, destroy) — add guard: `[ -n "$VAR" ] || exit 1`
- `grep` returning empty silently — downstream commands get empty input

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

## OpenTofu / Terraform

### State
- Parsing `tofu state show` text output is fragile — use `tofu output` instead
- Missing outputs that scripts need — add them to main.tf
- Snapshot/image IDs in tfvars after deleting the snapshot — stale reference

### Destructive Operations
- Validate resource IDs before destroy: `[ -n "$ID" ] || exit 1`
- `tofu destroy` without `-target` destroys everything including reserved IPs
- Snapshot ID extraction by name: use `awk -v n="$NAME" '$2 == n {print $1}'` (exact match on column 2). `grep -F "$NAME"` is substring-match and can grab a stale snapshot whose name contains the new name as a substring.

### "Has been deleted" in plan output is not authoritative — verify against the cloud API first
- The AWS provider (5.x and some 6.x) has a known class of bug where a transient read error (false 404, regional-endpoint hiccup) is interpreted as "resource deleted outside of OpenTofu." The plan will show the resource and any children scheduled for destroy + recreate (`forces replacement` cascades through children that interpolate the parent's id/arn).
- If you didn't delete the resource and the plan says it's gone, **verify against the cloud API before applying**: `aws s3 head-bucket --bucket X`, `aws iam get-role --role-name X`, etc. A `tofu plan -refresh=true` re-run a moment later often reports "No changes."
- Caught 2026-05-14 in rtj env/prod for stac-era5-land: bucket fully intact (60 objects, 307 MB) but plan said deleted with 5 child resources "must be replaced." Apply would have clobbered the policy + lifecycle configs against the still-existing bucket. Recovery via `-target` on the unrelated resource being added (rtj#157 then codifies `lifecycle { prevent_destroy = true }` on the bucket + load-bearing children).
- **Belt-and-suspenders defense:** add `lifecycle { prevent_destroy = true }` to high-value resources (S3 buckets, RDS instances, anything irreplaceable) in their module. Tofu will refuse to plan a destroy until the lifecycle line itself is removed in config — converts the failure mode from "apply silently clobbers" into "plan errors with `Instance cannot be destroyed`." Don't apply it to count-based resources where `count: 1 → 0` is a legitimate transition.

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

## R / Package Installation

### pak Behavior
- pak stops on first unresolvable package — all subsequent packages are skipped
- Removed CRAN packages (like `leaflet.extras`) must move to GitHub source
- PPPM binaries may lag a few hours behind new CRAN releases

### Reproducibility
- Branch pins (`pkg@branch`) are not reproducible — document why used
- Pinned download URLs (RStudio .deb) go stale — document where to update

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

## General

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


**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.


# New Graph Environment Conventions

Core patterns for professional, efficient workflows across New Graph Environment repositories.

## Ecosystem Overview

Six repos form the governance and operations layer across all New Graph Environment work:

| Repo | Purpose | Analogy |
|------|---------|---------|
| [compass](https://github.com/NewGraphEnvironment/compass) | Ethics, values, guiding principles | The "why" |
| [soul](https://github.com/NewGraphEnvironment/soul) | Standards, skills, conventions for LLM agents | The "how" |
| [compost](https://github.com/NewGraphEnvironment/compost) | Communications templates, email workflows, contact management | The "who" |
| [rtj](https://github.com/NewGraphEnvironment/rtj) (formerly awshak) | Infrastructure as Code, deployment | The "where" |
| [gq](https://github.com/NewGraphEnvironment/gq) | Cartographic style management across QGIS, tmap, leaflet, web | The "look" |
| [crate](https://github.com/NewGraphEnvironment/crate) | Data governance: canonical schemas, data dictionary, QC rules (scoping; normalization functions are Year 2+) | The "what" |

**Adaptive management:** Conventions evolve from real project work, not theory. When a pattern is learned or refined during project work, propagate it back to soul so all projects benefit. The `/claude-md-init` skill builds each project's `CLAUDE.md` from soul conventions.

**Cross-references:** [sred](https://github.com/NewGraphEnvironment/sred) tracks R&D activities across repos. Compost is the centralized communications workflow — all email drafts, contact registry, and external outreach are authored there, not in individual project repos.

## Three-Layer Repo Architecture

Repos live in one of three layers, distinguished by audience and what context they carry:

| Layer | Role | Examples |
|---|---|---|
| **Public — tools** | Atomic, reusable, no NGE-specific context | R packages (`mc`, `crate`, `fresh`, `drift`, `flooded`, `gq`, `link`), `bcfishpass`, `fwapg`, STAC catalogs, post-publication reports |
| **Private — coordination** | How tools compose into NGE workflows. The competitive moat. | `compost` (uses `mc`), `rfp` (uses `fresh`/`link`/etc.), `rtj` (uses `crate`, deploys), `fish_passage_template_reporting`, all proposals (never public) |
| **Private — governance** | Strategy, values, conventions, R&D | `soul`, `logic`, `compass`, `sred` |

**Rule:** tools don't know about each other or about NGE. Coordination repos know how to use tools. `mc/CLAUDE.md` does not know `compost` exists; `compost/CLAUDE.md` knows "for email use `mc`."

**Publication flip:** when a private repo flips public (e.g., `crate` once `link` requires it; reports on publication), three things happen in the same commit: removed from comms peer list, `comms/` directory purged, `CLAUDE.md` scrubbed to public-safe form. Use `/claude-md-init --public-clean` for the scrub.

**Per-repo classification** is recorded in `.claude/visibility` (one line: `public` or `internal`; default `internal` if missing). Soul conventions carry `visibility:` frontmatter (`public-safe` or `internal`); `/claude-md-init` filter skips internal-only conventions when repo is marked public.

Strategic call recorded in `logic/comms/soul/20260428_public_vs_internal_repo_architecture.md`.

## Issue Workflow

### Before Creating an Issue (non-negotiable)

1. **Check for duplicates:** `gh issue list --state open --search "<keywords>"` -- search before creating
2. **One issue, one concern.** Keep focused.

SRED cross-refs go in **PR bodies only** (via `/gh-pr-push`), not in issues or commits. PRs aggregate commits and are the merge unit; per-issue and per-commit SRED tags add noise without adding traceability.

### Professional Issue Writing

Write issues with clear technical focus:

- **Use normal technical language** in titles and descriptions
- **Focus on the problem and solution** approach
- **Add tracking links at the end** (e.g., `Relates to Owner/repo#N`)

#### Client-aware tone

Issues, PR descriptions, and commit messages are client-visible deliverables, not internal notes.

Avoid in these artifacts:
- Framing work as unsolicited or unpaid ("not assigned by a client")
- Self-justifying adjectives ("defensible", "rigorous") — show, don't claim
- Internal workflow meta (PWF refs, SRED xrefs, planning context)
- Performative effort language ("attempts were unsuccessful") — state factual current state

**Integrity-preserving ≠ self-effacing.** Factual, not performatively humble.

**Scope:** repo artifacts (issues, PRs, commits, reports). Does not apply to internal planning docs, CLAUDE.md, or chat.

**Issue body structure:**
```markdown
## Problem
<what's wrong or missing>

## Proposed Solution
<approach>

Relates to #<local>
```

#### Infrastructure references

Use **tailnet hostnames** (`cypher`, `m1`, `openclaw`) in issue and PR bodies, not public IPs. Within NGE infrastructure, those hostnames are how scripts and operators address machines anyway; the public IP is an implementation detail that belongs in gitignored `*.tfvars` and the Tailscale admin panel.

Public IPs in issues are appropriate only when the IP itself is the subject — reserved-IP migrations, DNS records, firewall rules that key on a specific IP. For everything else, use a placeholder like `<cypher_public_ip>` if the shape of the value matters at all.

Aggregation is the risk: any single IP in a private repo is fine, but issue bodies tend to collect IP + hostname + service description + access path into a coherent attack-surface map. Tailnet hostnames keep the map terse.

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

### Which logs to commit

Logs are R&D evidence — but only the curated ones. Distinguish two classes:

- **Evidence logs** (commit): the dated, conventionally-named runs at the top of a `logs/` dir
  (`yyyymmdd_noun_verb-detail_target.ext`). One benchmark/timing/run log per intentional run. These
  are committed to the **default branch** — git gives free versioning and commit-provenance (the log
  sits next to the change that produced it), and committed logs are discoverable cross-machine via the
  GitHub API without cloning.
- **Bulk run-output** (gitignore): archives, per-shard/per-watershed dumps, aborted/offline reruns, and
  other machine-generated iteration output. Put these under a gitignored subdir (e.g. `logs/runs/`,
  `logs/archive/`) so they never bloat the repo.

Rules of thumb: if you'd hand it to an auditor as proof of an experiment, commit it. If it's hundreds of
files a pipeline emitted, gitignore it. Don't reach for S3 for text logs — git is the right home;
external object storage only earns its place for large binaries. Logs that aren't committed to the
default branch are invisible to other machines and to evidence tooling — so commit evidence logs
**before** moving machines, or it's stranded.

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
3. **Plan-review with the Plan agent before committing the plan** — After scaffolding `task_plan.md` but BEFORE the baseline commit, spawn the Plan subagent (`Agent({subagent_type: "Plan", prompt: "..."}`) and ask it to critically review the task_plan against the issue body + actual codebase. Categorize findings as Blocker / Gap / Ordering / Assumption / Scope / Acceptance. Address each before committing. The agent reads files fresh — it catches what you miss when you've been thinking about the design too long. Real example: caught 21 issues including hardcoded literals across 4 files not listed in the plan, untested DB column mismatches, unfixable test-literal-string assertions, and a baseline-cache-shadow that would have produced a 6-second no-op run. Cost: ~5 min agent. Saves: hours of mid-implementation rework.
4. **Commit the plan** — After Plan-agent review + fixes. This is the baseline.
5. **Work in atomic commits** — Each commit bundles code changes WITH checkbox updates in the planning files. The diff shows both what was done and the checkbox marking it done.
6. **Code check before commit** — Run `/code-check` on staged diffs before committing. Don't mark a task done until the diff passes review.
7. **Archive when complete** — Move `planning/active/` to `planning/archive/` via `/planning-archive`. Write a README.md in the archive directory with a one-paragraph outcome summary and closing commit/PR ref — future sessions scan these to catch up fast.

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


# SRED Conventions

How SR&ED tracking integrates with New Graph Environment's development workflows.

## The Claim: One Project

All SRED-eligible work across NGE falls under a **single continuous project**:

> **Dynamic GIS-based Data Processing and Reporting Framework**

- **Field:** Software Engineering (2.02.09)
- **Start date:** May 2022
- **Fiscal year:** May 1 – April 30
- **Consultant:** Boast Capital (prepares final technical report)

**Do not fragment work into separate claims.** Each fiscal year's work is structured as iterations within this one project. Internal tracking (experiment numbers in `sred`) maps to iterations — Boast assembles the final narrative.

## Tagging Work for SRED

### PRs (single enforcement point)

SRED cross-references (`Relates to NewGraphEnvironment/sred#N`) go in **PR body templates only** — not in issue bodies, commit messages, or any other surface. The `/gh-pr-push` skill is the single enforcement point. PRs aggregate commits and are the merge unit, so per-issue and per-commit SRED tags only add noise.

### Time entries (rolex)

Tag hours with `sred_ref` field linking to the relevant `sred` issue number.

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
