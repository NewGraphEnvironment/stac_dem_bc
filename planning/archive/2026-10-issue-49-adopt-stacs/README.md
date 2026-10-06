## Outcome

Registration and verification moved from this repo's scripts to the `stacs` package,
pinned at `v0.1.0`. The catalogue declares itself in `stacs.toml`: API, collection id,
bucket, asset rules (`dem` required, `image` forbidden) and transport. Because those
values also live in modules, `tests/test_stacs_config.py` pins each one to its module. It
also pins `collection_unregister.sh`'s host and db, and checks that the install came from
the tag.

The workflow's two audit steps now run `stacs audit`. They check items against the
declared collection id, and the asset rules apply on backfill runs too.
`catalogue_register.sh`, `item_register.sh` and `collection_register.sh` are deleted, as
is all of `register_manifest.py` except `ids-from-urls`. #42's own-bucket inference is
gone; a declaration replaces it.

What was learned:
- `ids-from-urls` had never worked on the repo's real URL lists. They store `https:/`
  (one slash), because `ngr_s3_keys_get()` builds URLs with `fs::path()`. The
  root-cause issue is proposed for ngr, and the cache migration for here.
- NEWS' Unreleased section described #42/#45 tooling that never shipped. It was
  rewritten as one entry.
- Every test fixture sorted the stale item last, so an audit that checked only the
  last item passed all of them. Moving the stale item to the middle fixed it.

## Measurement

The live check against images.a11s.one ran with the committed config:
- `stacs verify`: IN SYNC, 102,460/102,460, 0 missing, 0 orphaned, 0 changed,
  collection `same`, in 16m29s.
- `stacs register --mode drift`: probed the API and ssh to `root@geopro`, then found
  nothing to register, in 16m37s.

Both are within the 12–26 min the old scripts took on 2026-09-29, so switching tools did
not change the cost. The remote load script itself (`env_file`, `uv run pypgstac`,
`STACS_LOADED`) was **not** exercised, because nothing needed writing.

Review loop:
- Phase 1: three `/code-check` rounds. Round 2 found a defect inside round 1's fix: a
  `match="password"` that matched pytest's tmp dir name. Round 3 enumerated all 23
  asserts and found the sort-order blind spot. The loop ended when a re-run of the full
  set of 34 mutations turned every one red.
- Phases 2–4: two rounds. They found 5 stale docs, including the Pages landing page and
  an overstated audit scope, then 2 wording errors. The loop ended by enumerating every
  added claim about URL form and `--dryrun`.

## Evidence

`logs/20261006_14*_stacs_*_49.log` (gitignored; on the machine that ran them). Reviews:
`review-*.md` in this directory.

Closed by: PR #50
