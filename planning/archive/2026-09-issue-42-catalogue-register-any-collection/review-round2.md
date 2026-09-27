# Code review, round 2 (#42)

Reviewer: general-purpose subagent, 2026-09-27. Scope: `diff2.txt` (staged changes to
`scripts/catalogue_register.sh`, `scripts/register_manifest.py`,
`tests/test_catalogue_register.py`), with focus on `s3_bucket_name`, `same-bucket` and
the `SAME_BUCKET` case-arm. Full suite: `263 passed in 30.56s`.

## Findings

- **[fragile, low] scripts/catalogue_register.sh:110-116 / scripts/register_manifest.py:356-379.**
  The bucket half of "this repo's catalogue" is decided from how the operator spelled
  `STAC_BUCKET_URL`, before anything is fetched. Every S3 alias the round-1 review
  named now parses correctly. But any spelling that is not a recognised S3 URL
  answers `different`, which is the foreign branch with no asset audit. Combined with
  `STAC_COLLECTION=<old id>` (the rename-window case the bucket clause exists for),
  these still load old-shape items unchecked:
  - `file://` pointing at a local copy of the bucket or a local build. The harness
    itself uses this form. The item hrefs inside that `collection.json` are still
    `https://stac-dem-bc.s3.amazonaws.com/...`, so the items are fetched from this
    bucket.
  - A CNAME or CloudFront front for the bucket. None is known today.
  - A VPC interface endpoint, which is mis-parsed:
    `s3_bucket_name("https://bucket.vpce-1a2b.s3.us-west-2.vpce.amazonaws.com")` returns
    `'bucket.vpce-1a2b'`, not `'bucket'`. It cannot be reached from a laptop on the
    tailnet, so this is theoretical here.

  A trailing-dot FQDN (`stac-dem-bc.s3.amazonaws.com.`) also parses to `None`, but I
  probed it with HEAD and it returns 404, so the fetch fails first. It is not a live
  hole.

  All of these need an operator who also sets `STAC_COLLECTION` to a non-current id,
  which the mismatch error explicitly tells them not to do. So this is a narrowing of
  the pre-#42 guarantee, not a routine-path bug. The fact that cannot be aliased is
  already on disk after the fetch: the item link hrefs in `$WORK/collection.json`. Per
  CLAUDE.md, all 102,460 of them start with `PATH_S3_STAC`. If the ownership test must
  be airtight, check those hrefs after the fetch, as round 1's third option suggested.
  If not, record the residual in the header comment, which currently says the rename
  window "is refused as it was before #42" without qualification.

## Checked and clean

- **Own catalogue as foreign, via the default or the listed aliases.** The default
  `BUCKET_URL` equals `OWN_BUCKET_URL`, so it is always `same`. Every one of these
  parses to `stac-dem-bc`:
  - regional, `http`, path-style, upper-case host
  - `:443`, `S3.` in upper case, `s3-website-*`, `-accelerate`, dualstack, `s3-<region>`
  - `s3://`, and surrounding whitespace or a newline

  I checked live that dualstack returns 200. Path-style on the global endpoint returns
  301, so curl fails on it anyway.
- **Import noise on stdout.** Nothing prints on import (`od -c` on the
  `PATH_S3_STAC` lookup shows exactly one line), so `OWN_BUCKET_URL` cannot pick up
  junk that would make `same-bucket` answer `different`.
- **Exceptions.** A malformed URL (`https://[::1`) makes `urlsplit` raise, which exits 1
  with empty stdout. The case-arm rejects it and the script exits loudly. It fails
  closed, as designed.
- **Foreign catalogue as ours.** This needs the parsed bucket to equal `stac-dem-bc`,
  which a foreign bucket does not. The only reachable cases:
  - Crafted URLs such as `s3.../stac-dem-bc/../other`. curl collapses the dot
    segment, so the script fetches `other` while the parse says `stac-dem-bc`.
  - Upper-case path-style `/STAC-DEM-BC`. S3 would not serve it.

  Both fail in the refusing direction and neither is plausible operator input. The
  lookalikes in the tests (`-dev` suffix, the name in the path of a foreign host,
  `.example.com`) all return something other than `stac-dem-bc`. Dotted buckets are
  split at the last `.s3` label correctly, for example
  `x.s3.amazonaws.com.s3.amazonaws.com` gives `x.s3.amazonaws.com`.
- **`same` needs `a is not None`.** Two non-S3 URLs (the `file://` fixtures) cannot
  compare equal.
- **Mutation, in a scratch copy.** I replaced the `same-bucket` answer with a constant
  `different`, and 7 tests went red, including the path-style and upper-case
  rename-window e2e cases. The guard fires.
- **Bash 3.2 details.** The `case` arm, `${!var:-}`, and the
  `${AUDIT_ASSET_ARGS[@]+...}` form under `set -u` are all fine.
- **Audit order.** The audit now precedes `collection_register.sh`, and both still read
  the same `$WORK` artifacts.
- **Callers.** `.github/workflows/update.yml` does not invoke the script (it appears
  only in a comment), so CI is unaffected.

---
## Triage (parent session)

Bucket-URL spellings that are not S3 URLs (file:// copy, CNAME/CDN, VPC endpoint) — **real, inside round 1's fix** (second consecutive inside-a-fix finding). Fixed at the mechanism rather than the parser: a third ownership fact, checked after the collection.json fetch and before any write or dryrun exit — any item link whose href is in this repo's bucket (`register_manifest.py hrefs-in-bucket`). Tests: 2 e2e (dryrun shows the upgraded rules; env override refused before the item fetch). Mutation (check disabled) → both red.

Enumeration that terminates the class: item bodies reach the audit only via collection.json item hrefs. Live collection.json, 2026-09-27: 102,460 of 102,460 hrefs parse to `stac-dem-bc`; every writer of item links builds them from `PATH_S3_STAC` (item_create.py:188, item_rewrite.py:191). Evading (3) needs hrefs hand-rewritten to an unparseable alias — deliberate, outside the threat model.
