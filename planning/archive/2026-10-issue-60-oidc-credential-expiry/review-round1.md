# Code-check review, round 1 (#60)

Scope: `git diff main...HEAD` on `60-update-yml-oidc-credentials-expire-an-ho`, i.e.
`.github/workflows/update.yml` and `tests/test_workflow_credentials.py`.

## Findings

- **[fragile]** tests/test_workflow_credentials.py:27-31 (`uses_aws`): the detector sees
  only two spellings of AWS use, so the module's claim ("Every AWS write in update.yml
  runs on a credential fetched just before it") passes vacuously for any other spelling.
  Not a defect in the workflow as it stands (see the verification below: the sync is the
  only AWS consumer today), but the guard fails toward pass for the next one. Measured in
  a temp copy of the repo, each mutation inserted as a new step just before
  `Upload run logs` with no credentials step before it:

  | mutation | result |
  |---|---|
  | M1: delete the pre-sync re-assume step (the #60 bug restored) | 2 failed: caught |
  | M2: `always() &&` added to the re-assume step's `if:` only | 1 failed: caught |
  | M5: `run: aws s3 cp data/x s3://stac-dem-bc/x` | 1 failed: caught |
  | M3: `run: \|` block with `if aws s3 ls ...` and `echo done && aws s3 cp ...` | **3 passed** |
  | M4: `run: .venv/bin/aws s3 cp ...` | **3 passed** |
  | M6: `run: .venv/bin/python -c "import boto3; ...upload_file(...)"` | **3 passed** |

  `line.strip().startswith("aws ")` misses `aws` that is not the first token on its line
  (`if aws`, `&& aws`, `$(aws ...)`, a path-qualified `aws`), any SDK use from Python or
  R, and any `uses:` action that talks to S3; `"s3_sync-ci.sh" in run` covers only the
  one script. So the test pins the shape of today's sync step, not the property. If it is
  meant as the standing guard against a second AWS step reintroducing #60, it needs a
  wider net (e.g. match `\baws\b` anywhere in `run`, plus `boto3`/`s3://` writes); if it is
  only meant to pin the sync step, the docstring over-claims. Low severity.

## Verified, no finding

- **"The sync is the only step that uses AWS credentials" holds.** Every other S3/network
  read in the job is anonymous:
  - `detect_changes.R` -> `urls_listing.R` -> `ngr::ngr_s3_keys_get()`, which is a plain
    `httr::GET` on `https://nrs.objectstore.gov.bc.ca/gdwuts` (read the installed source):
    unsigned, and not even AWS.
  - `Fetch current collection` and the rebuild step's fallback: `curl -fsSL https://stac-dem-bc.s3.amazonaws.com/collection.json`.
  - `item_rewrite.py` (shared by backfill / migrate / footprint_apply): `urllib.request.urlopen`
    on `PATH_S3_STAC = https://stac-dem-bc.s3.amazonaws.com`; published ids come from the
    collection's item links, not a bucket listing.
  - `footprint.py` / `footprint_extract.py` / `item_create.py`: `urllib` downloads and
    rasterio `/vsicurl/` on https URLs; no `/vsis3/` anywhere in `scripts/`, and GDAL does
    not sign `/vsicurl/`.
  - `stacs` (installed v0.1.0): no boto/botocore; its only subprocess is ssh in
    `register.py`, which the workflow never calls. boto3/botocore/s3fs are not installed in
    the venv at all.
  - The commit step uses git with the checkout token only; `upload-artifact` is GitHub.
  - Repo-wide grep for `boto|vsis3|s3://|aws|paws|aws.s3|AWS_|s3fs` in scripts: the only
    hits outside `s3_sync-ci.sh` are laptop tools not called by the workflow
    (`s3_sync.R`, `catalogue_qa.py`, `benchmark_fetch.R`).
- **A second `configure-aws-credentials@v4` call does a fresh web-identity assume and
  overrides the first.** Read the action at tag v4 (`src/index.ts`, `assumeRole.ts`,
  `CredentialsClient.ts`): `useGitHubOIDCProvider()` keys on the `role-to-assume` input,
  `ACTIONS_ID_TOKEN_REQUEST_TOKEN` (present: `id-token: write` at workflow level) and the
  absence of the `aws-access-key-id` *input* and `role-chaining`, not on env credentials.
  It fetches a new ID token, calls `AssumeRoleWithWebIdentity` (an unsigned STS operation,
  so stale env credentials cannot interfere even if already expired), and
  `exportCredentials` overwrites `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` /
  `AWS_SESSION_TOKEN` via `core.exportVariable`. The credential-validation call is skipped
  on GitHub-hosted runners in the OIDC path. `unset-current-credentials` is not needed.
- **The `if:` on the new step behaves identically to the sync's.** Both are the same
  folded scalar, both carry the implicit `success()`, and the `steps.publish` /
  `steps.collpatch` outputs they read are fixed before either runs. No step references the
  new step (it needs no `id:`). If the re-assume fails, the sync is skipped by `success()`,
  `steps.sync.outcome` is `skipped`, and the commit step discards the rewrite manifests
  exactly as for a failed sync. The sync took ~11 min for 91,772 items in the failing run,
  so one fresh 1 h session covers a full ~102k-item upload with margin.
- Test run: `.venv/bin/python -m pytest tests/ -q` -> 313 passed (temp copy, original
  workflow restored and `cmp`-identical to the repo's before the run). The test runs in CI
  (`Run pairing contract tests` runs `pytest tests/`; `pyyaml` is installed there).
- The real repo was not modified: all mutations were in a `cp -r` under the scratchpad;
  `git status --short` in the repo was empty afterwards.

## Verdict

No bug or security issue in the workflow change. One fragile finding: the new test's AWS
detector is narrow enough to pass vacuously for most ways a future step could use AWS.
