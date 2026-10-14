# Code-check round 3 — #60 (update.yml re-assume + tests/test_workflow_credentials.py)

Reviewed 2026-10-14 (UTC) against branch `60-update-yml-oidc-credentials-expire-an-ho` at c44cd53.

## Mechanism

All three rounds' findings come from one assumption: **"uses AWS" is decided by
matching a list of spellings in the repo's own source text.** Each round widened the
list (line-start `aws` -> any command position -> quoted argv), and each miss was a
spelling the list did not hold yet.

Round 2 added a ground-truth test to pin the list. **It does not close the gap.**
`SIGNED` was "enumerated by hand ... from a grep for aws/boto/vsis3". That is the same
vocabulary the regex encodes, so the oracle and the detector share a blind spot. Any
spelling outside the vocabulary is missed by both, and `found == SIGNED` still holds.
This is the code-check pattern "Verification that reads its own output", and the
guard fails toward pass: when the detector misses a step, the precedence test has
nothing to check, and all four tests stay green.

Where the mechanism reaches in this diff:
- `AWS` regex (test_workflow_credentials.py:22). This is the vocabulary itself.
- `signs_aws` and `COMMENT` (:23, :45-47). These scan only what the vocabulary names.
- `sources()` (:50-65). It follows only `scripts/...` path literals, Python
  `import <name>` that resolves to `scripts/<name>.py`, and R `source("scripts/x.R")`.
  It does not follow `subprocess`, `processx`, `system2`, `python -m`, `cd scripts &&`,
  or a heredoc that runs a file.
- `uses_aws` (:68-79). It also matches only `aws-actions/*` for third-party actions.
- `SIGNED` and `test_the_detector_finds_exactly_...` (:32, :82-90). This is the
  circular oracle. It also scans only top-level `scripts/*.{py,sh,R}`, while the
  workflow also runs `tests/` (the pytest step) and the `stacs` CLI.

### Is SIGNED correct today? Yes. Checked independently.

I grepped with a wider vocabulary than the oracle's. The terms were
aws, boto, vsis3, s3fs, paws, aws.s3, `AWS_`, `s3://`, fsspec, obstore, minio,
s3cmd and ngr_s3. The scope was every .py/.sh/.R/.qmd/.toml/.yml/DESCRIPTION in the
repo, minus planning/ and .venv. Each hit falls into one of four classes:

- **Signed AWS calls.** There are three: `s3_sync-ci.sh:57,61` (aws CLI),
  `s3_sync.R:14` (`paste0("aws s3 sync ")`, run by `processx`) and
  `catalogue_qa.py:36` (`["aws","s3","cp"]`). This matches SIGNED exactly.
- **`ngr::ngr_s3_keys_get`** (urls_listing.R, called from detect_changes.R, which the
  workflow runs). I read the installed ngr 0.0.3 source. The function is an
  anonymous `httr::GET` against the BC objectstore, not a signed call.
- **`s3://`** appears only in comments and in CLI defaults that the aws CLI consumes
  (catalogue_qa.py:103, s3_sync-ci.sh:32). `s3fs` appears only in a comment
  (s3_sync.R:4).
- **Everything else** is an `https://…s3.amazonaws.com` URL, or a test-data string
  in test_collection_identity.py.

The installed `stacs` package has no boto3, s3fs or obstore, so the early-probe
claim holds. That claim is "nothing before the sync needs AWS auth (every other S3
read is anonymous HTTPS)", and I confirmed it step by step: every pre-sync step reads
with curl over https, httr, or Python https, or works on local files.

So there is **no false negative today**. The only AWS-using step the workflow reaches
is `Sync catalog to S3`, and the test finds it.

### Comment stripping

`^\s*#.*$` removes only lines that *start* with `#`. A false negative needs an AWS
call on such a line that is still live code: inside a heredoc, a Python triple-quoted
string, or an R string. None exists. Stripping also makes no required detection
depend on luck. I re-ran the regex over the stripped text, and every SIGNED hit comes
from a code line:

- s3_sync-ci.sh: the `aws s3 sync` and `aws s3 cp` lines
- s3_sync.R: the `"aws s3 sync "` line
- catalogue_qa.py: the `["aws", "s3", "cp", …]` line

None of them comes from a comment. Trailing comments are not stripped, so they can
only cause a false positive, which fails loudly.

## Findings

- **[severity: fragile]** tests/test_workflow_credentials.py:22, :28-32, :82-90 — The
  ground-truth test cannot catch the case it was added for. A new pre-sync script
  that calls AWS in a spelling outside the vocabulary passes all four tests, and
  then runs on the probe's credential, which may have expired.

  Proof by mutation in a temp copy. I added `scripts/probe_new.py` containing
  `subprocess.run(["aws"] + ["s3","ls"])` and a step `run: .venv/bin/python scripts/probe_new.py`
  just before the re-assume step: **4 passed**.

  I also ran `signs_aws` on each of the following spellings, and it returned False
  for all of them:
  - `aws \` with a line continuation
  - `"$AWS" s3 sync`
  - `["aws"] + args`
  - `shutil.which("aws")`
  - `processx::run("aws", c(...))`
  - `system2("aws", …)`
  - `paws::s3()`
  - `aws.s3::put_object`
  - `import botocore`, `import aioboto3`
  - `s3fs.S3FileSystem`
  - `obstore` S3Store
  - `pyarrow.fs.S3FileSystem`
  - `rasterio.open("s3://…")`, which GDAL signs through /vsis3/

  None of these exists in the repo today, so this is not a live bug. But the guard
  fails toward pass, and widening the regex again would be round 4 of the same
  mechanism.

  The way out is to test the property at run time rather than a proxy. The early
  probe currently exports `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and
  `AWS_SESSION_TOKEN` to every later step. That is why an un-preceded AWS call
  "works" for up to an hour and fails only on long runs. If the probe did not leave
  a credential in the environment, any AWS use without a re-assume in front of it
  would fail on *every* run, not only on long ones. Two ways to do that:
  - the action's `output-env-credentials: false`, if the pinned v4 supports it (check
    before relying on it), or
  - a step right after the probe that writes empty `AWS_*` values to `$GITHUB_ENV`.

  The static test would then be a second line of defence rather than the only one.

- **[severity: fragile]** tests/test_workflow_credentials.py:83-90 — The ground-truth
  scan covers only top-level `scripts/*.{py,sh,R}`. The workflow also runs code from
  `tests/` (the "Run pairing contract tests" step) and from the `stacs` CLI.
  `sources()` also does not follow pytest, so a test file that signs an AWS call
  would never be checked. Today `tests/` holds only bucket strings, so this is clean
  now and open in the future. It is the same mechanism: the oracle's scope was chosen
  together with the detector's scope.

### update.yml — no regression

Checked against `git diff main...HEAD`:
- The early probe step is unchanged; only a comment was added.
- The new configure-aws-credentials step sits immediately before `Sync catalog to S3`.
- The new step has the same role ARN, the same region, and a byte-identical `if:`
  (both are `>-` folded).
- Its implicit `success()` matches the sync's. If the re-assume fails, the sync is
  skipped, so `steps.sync.outcome` is `skipped`. The commit step then discards the
  rewrite manifests, which is the intended behaviour.
- `id-token: write` is still granted.
- Nothing else in the workflow moved.

The 1 h window is enough. The failed run uploaded 91,772 items in about 11 minutes,
so a full ~102k sync is about 13 minutes.

Mutations 1 and 2 behave as intended:
- removing the re-assume step fails the precedence and role tests
- dropping its `if:` fails the precedence test
