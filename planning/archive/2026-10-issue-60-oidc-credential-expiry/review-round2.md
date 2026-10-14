# Code-check round 2 (#60): the widened `uses_aws` detector

Scope: `tests/test_workflow_credentials.py` `AWS` regex and `sources()`, against
`.github/workflows/update.yml` at 9436795. Mutations were run in a scratch copy;
nothing in the repo was edited except this file.

## Findings

- **[fragile] tests/test_workflow_credentials.py:21 — the `AWS` regex misses every
  programmatic call of the CLI, which is the form this repo's own scripts use.**
  The character before `aws` must be in `[\s;&|(/`]` and `aws` must be followed by
  whitespace. A quoted command fails both: Python `subprocess.run(["aws", "s3", "cp", ...])`
  has `"` before and `",` after, and R `paste0("aws s3 sync ", ...)` /
  `processx::run(command = "aws")` has `"` before.
  - `scripts/catalogue_qa.py:36` signs S3 reads exactly this way and the detector
    returns **False** for it. Reproduced in a temp copy: adding a step
    `run: .venv/bin/python scripts/catalogue_qa.py --max-items 10` just before
    `Upload run logs` (after the sync, no credentials step before it) leaves all 3
    tests passing.
  - `scripts/s3_sync.R` is detected today only through two prose comments
    (`# lets just use aws to load ...`, `# aws s3 sync attempts ...`). Delete those
    two comment lines and `uses_aws({"run": "Rscript scripts/s3_sync.R"})` is
    **False**, although the script runs `aws s3 sync --delete`.
  - This is the guard failing toward pass: the step it misses is precisely a new
    QA/sync step, the most likely future AWS step in this workflow.
  - A fix that holds on the current tree: allow a quote before `aws` and a
    quote-comma-quote after it, e.g.
    `(^|[\s;&|(/`"'])aws(\s+|["']\s*,\s*["'])[a-z]|\bboto3\b|/vsis3/`.
    Measured: on the current workflow it still flags only `Sync catalog to S3`, and
    it matches `catalogue_qa.py` (`"aws", "s`) and `"aws s3 sync "`.

- **[fragile] tests/test_workflow_credentials.py:36-49 — `sources()` follows Python
  imports but not R `source()`.** `Detect changes` runs `scripts/detect_changes.R`,
  which does `source("scripts/urls_listing.R")` (detect_changes.R:31; the S3-key
  listing code); `sources()` returns only `detect_changes.R`, so AWS use added to
  `urls_listing.R` would not be seen. Not a live miss today (`urls_listing.R` has none),
  lower likelihood than the first finding. If fixed, scope it to R `source("scripts/...")`
  calls rather than applying `SCRIPT.findall` to every scanned file: docstrings such as
  `catalogue_qa.py:13` (`python scripts/catalogue_qa.py`) name scripts in prose, and a
  comment in a widely imported module naming `scripts/s3_sync-ci.sh` would then mark
  every item step as AWS and break the suite.

## Checked and not a problem

- Folded `run: >` / `>-`: folding turns newlines into spaces, which the `\s` class and
  `re.M` `^` both still match.
- Loops: `seen` guards revisits; imports resolving to stdlib names are skipped by
  `os.path.exists`.
- Encoding: every scanned file decodes as UTF-8 locally, and the in-CI run (Python 3.12
  on ubuntu, C.UTF-8) uses UTF-8 mode; no crash path found.
- No comma-form `import a, repo_module` exists in `scripts/`, so `IMPORT` taking only
  the first name loses nothing today.
- `python -m`, `cd scripts && ...`, `env:`-only steps: none in update.yml; not reported.
- Current tree: the detector flags exactly `Sync catalog to S3`; all 3 tests pass.
