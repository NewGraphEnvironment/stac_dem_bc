## Outcome

`scripts/catalogue_register.sh` now registers any collection on the endpoint, not only this repo's. Its pre-load audit required a `dem` asset of every item, so stac-airphoto-bc was refused in full. The asset half of the audit now follows the catalogue: this repo's keeps `require=dem forbid=image`, with env overrides refused. Any other catalogue gets `STAC_REQUIRE_ASSET` / `STAC_FORBID_ASSET` if set, and prints `asset audit: none` when not. The collection-id and count checks always run.

Exploration also found that the audit ran *after* the collection upsert, so a refused run had already written the collection row. It now runs before. `audit-items` prints the rules it actually applied.

The lesson is in how "whose catalogue is this" got decided, which took four reviews to get right. Keyed on the collection id alone, it reopened #34's rename window. The fix, the bucket URL, missed aliases (round 1). The fix for that, bucket name, missed non-S3 spellings such as a `file://` copy or a CNAME (round 2). Each fix added a spelling without changing which way an unrecognised input falls, and it falls toward "foreign", the branch with no asset audit. What ended it was the fact that cannot be spelled around, the item hrefs the fetch actually reads, plus an enumeration showing every published href carries it. Round 3 found no defect inside that fix. It named the mechanism and recorded one latent, fail-closed over-classification, which is now in #35's body.

## Measurement

- stac-airphoto-bc, `audit-items` over `stac_airphoto_bc/data/stac/` (read-only): **10,100 of 10,100** pass under the new rules, and **10,100 of 10,100** fail `lack asset 'dem'` under the old ones. This reproduces the issue's figure.
- Live `collection.json` for this repo (2026-09-27): **102,460 of 102,460** item hrefs parse to bucket `stac-dem-bc`. This is the enumeration that terminated the review loop, confirmed independently by review round 3.
- Live, read-only `--verify` against airphoto through the new path: IN SYNC, 10,100 published, all registered, no orphans.
- Mutations: 5 were run and each turned its target tests red. Hard-coded dem turned 3 red, the old order 8, an honoured env override 2, the string bucket compare 4 (the aliases), and the href check off 2.
- Suite: 265 passed under `/bin/bash` 3.2. The e2e tests had run Homebrew bash 5 until round 3 caught it.
- Wrong turn: a harness bug scrubbed the fixture `STAC_BUCKET_URL` after setting it, so a test run started fetching the real bucket's ~102k items. It was killed and fixed, and a `curl` stub now refuses any non-`file://` URL.

## Evidence

`review-*.md` in this directory: the plan review and code-check rounds 1–3, each with its triage.

Closed by: PR for #42 (branch `42-catalogue-register-sh-audits-for-a-dem-a`). Follow-up: NewGraphEnvironment/stac_airphoto_bc#33.
