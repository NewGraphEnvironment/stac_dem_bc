# Code-check round 3 — #42 (2026-09-27)

Reviewer: general-purpose subagent. No repo files edited; probes ran in a scratch copy.

## Verdict

There are no bugs or security issues in the diff. One fragile item, which is latent and
fails closed. There is also one correction to the parent's enumeration, which does not
change its conclusion.

## The mechanism behind plan-review Gap 1, round 1 and round 2

**Whose catalogue this is gets decided from addresses, and anything the classifier does
not recognise falls through to "foreign", which is the branch with no asset audit.**
An address here is the collection id string, the bucket URL, or the item hrefs. Every
address has more spellings than a parser knows:

- the id has a history (the rename window)
- the bucket has endpoint and scheme aliases (round 1)
- the URL may not be S3 at all (round 2)

So each unrecognised spelling fails toward pass. Each fix so far added one more
recognised address. None of them changed the fall-through direction, and none looked
at the content.

The one fact that is not an address is the item bodies themselves. The audit loads
them, and I checked one live body: every body carries
`links[rel=collection] = https://stac-dem-bc.s3.amazonaws.com/collection.json`
(written at stac_utils.py:371) and a `dem` asset on the objectstore. An ownership check
over the fetched bodies would close the class, including the accepted
"hand-rewritten hrefs" tradeoff. I am offering that as the mechanism-level option, not
as a required fix: the three facts are sound for every input that occurs today.

### Every place the mechanism reaches in this diff

| # | Location | What it classifies by | Status |
|---|---|---|---|
| 1 | catalogue_register.sh:153 (fact 1) | exact id string, current id only | Closed for the #34 window by facts 2 and 3. **Open by design** for a future move of the id and the bucket together: fact 1 knows only the current id and facts 2 and 3 know only the current bucket, so Gap 1 comes back. Nothing plans such a move, so I am not filing it as a finding. |
| 2 | catalogue_register.sh:147-156; register_manifest.py:281-304, 530-535 (fact 2) | a partial S3-URL parser whose fall-through is `different` | Open by construction. Newly measured spelling: a trailing-dot FQDN host (`https://stac-dem-bc.s3.amazonaws.com./`) reads as `different`. Closed in practice because fact 3 backs it up. |
| 3 | catalogue_register.sh:254-265; register_manifest.py:537-543 (fact 3) | the same parser, applied to the item hrefs | This fact has no backup of its own. Closed today: the live collection.json (fetched 2026-09-27) is `id` stac-elevation-bc, and 102,460 of its 102,460 item hrefs are on host `stac-dem-bc.s3.amazonaws.com` and parse to `stac-dem-bc`. An unparseable **own** bucket URL is a hard error here (fails closed). |
| 4 | register_manifest.py:535 (`None == None`) | — | Correct: two non-S3 URLs compare `different`. |
| 5 | the else branch at catalogue_register.sh:157-164 | — | This is the permissive default itself. It says so out loud (`asset audit: none ...`, and `(no asset checks)` from audit-items), so it is not silent. |
| 6 | fetch at catalogue_register.sh:411 (`curl -sfL`) | — | The claim "bodies reach the audit only via collection.json hrefs" holds, with one caveat: `-L` follows redirects, so the body audited is whatever the href redirects to. S3 REST endpoints do not redirect, so reaching this takes deliberate configuration, which is out of scope. |

### Correction to the triage enumeration

The triage cites `item_rewrite.py:191` as a writer of item links. That line is
`item_fetch`, which builds a **fetch** URL and writes no links.

The actual writers of collection.json item links are:

- `item_create.py:188` → `:390` (built from `PATH_S3_STAC`)
- `stac_create_item.qmd:331`, which uses its own literal
  `path_s3_stac = "https://stac-dem-bc.s3.amazonaws.com"` (`:110`). That is a second
  definition, not `PATH_S3_STAC`. The triage missed this writer.

Both produce the canonical, parseable spelling, so the triage's conclusion stands.

## Findings

- **[severity: fragile]** catalogue_register.sh:155-156 and :260-261,
  register_manifest.py:291-304 — **Bucket-level identity is coarser than catalogue-level
  identity. This is the same mechanism, pointing the other way.**
  - `s3_bucket_name` discards the path, so any catalogue whose collection.json or item
    hrefs live anywhere in the `stac-dem-bc` bucket is classified as "this repo's".
    Measured: `same-bucket https://stac-dem-bc.s3.amazonaws.com/pointcloud <own>` →
    `same`.
  - Such a catalogue is then required to carry `dem` and forbidden `image`, and
    `STAC_REQUIRE_ASSET` / `STAC_FORBID_ASSET` are refused, so the operator has no
    escape.
  - This is not hypothetical in direction: #35 is open and its option 2 is "a separate
    collection with its own (finer) tiling" for the point cloud / CHM products. That
    repo publishes into this bucket. That collection would be refused in full, which
    is exactly #42's symptom, and this time with no knob.
  - It fails closed (refusal before any write), so there is no data loss and nothing
    to do before merge. It needs deciding when #35 lands. Keying facts 2 and 3 on the
    collection.json **location** rather than the bucket would close it. So would the
    body-level check above, if it were keyed on the `rel=collection` href rather than
    the bucket.

## Refactor into `use_own_asset_rules` / `describe_asset_rules`

- Checked:
  - `exit` inside both functions: they are called directly, never in `$(...)`, so the
    exit ends the script, and the EXIT trap (set at :187) cleans `$WORK` on the fact-3
    path.
  - `local var`: fine.
  - `${!var:-}` under `set -u` on bash 3.2: fine.
  - `AUDIT_ASSET_ARGS+=()` and the `${arr[@]+...}` expansion on an empty array under
    bash 3.2: fine.
  - `AUDIT_REQUIRE` and `AUDIT_FORBID` are always assigned before
    `describe_asset_rules` on every branch.
  - `AUDIT_OWN` is initialised before the first call.
- **Measured under /bin/bash 3.2.57:** in a scratch copy, the e2e test was switched to
  invoke `/bin/bash` and all 42 tests in `tests/test_catalogue_register.py` pass. The
  full suite on the real tree passes too: 265 passed, 35.7s.
- Observation, not a defect: as shipped, the e2e runs `bash` from PATH. On this machine
  that is Homebrew bash 5.3, so the suite never exercises the 3.2 interpreter that the
  `#!/bin/bash` shebang uses in real runs. The comment at :473 depends on 3.2 behaviour,
  and today only this manual run confirms it.

---
## Triage (parent session)

- Mechanism named (ownership inferred from addresses; unrecognised falls through to foreign). The live-href enumeration terminates the class for today: 102,460/102,460 hrefs in `stac-dem-bc`, confirmed independently by this round. No defect found inside round 2's fix → loop ends here.
- Bucket-level identity coarser than catalogue identity — **accepted, latent, fails closed**: refusal precedes any write. Recorded in #35's body, since the fix (key on collection.json location, or on item bodies' `rel=collection` link) depends on how #35 lays out its objects.
- Triage correction accepted: writers are `item_create.py:188→390` and `stac_create_item.qmd:331` (own literal); `item_rewrite.py:191` is a fetch URL. Conclusion unchanged.
- e2e tests now run `/bin/bash` (the shebang's 3.2 on macOS) rather than PATH `bash` (Homebrew 5.3).
- Trailing-dot host: 404 live (round 2), and fact 3 backs it. Not acted on.
