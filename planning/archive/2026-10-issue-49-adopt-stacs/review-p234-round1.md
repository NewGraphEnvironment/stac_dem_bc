# Review — #49 phases 2-4 (staged diff), round 1

Reviewed the staged tree (`git checkout-index` copy). stacs checked against the installed
package, which is byte-identical to tag `v0.1.0` for cli/validate/register/catalogue/verify
(the stacs working tree has uncommitted changes past the tag, so it was not used).

Verified, no finding:
- Full suite on the staged copy: `199 passed`.
- Both workflow audits: `.venv/bin/stacs` is the entry point `uv pip install` puts in
  `.venv/bin`; steps run from the workspace root (no `working-directory`), so the relative
  `stacs.toml` resolves. Exercised the exact command forms: good dir rc=0; `image`-keyed item
  rc=1 (missing `dem` + retired key); config/arg error rc=2. Both non-zero codes fail the step.
  `--expect ""` would be rc=2, but the monthly step is gated on `count != ''`.
- stacs audit is at least as strict as the old `audit-items`: same dir enumeration
  (`*.json` minus `collection.json`, sorted), plus `dem` must have an href, duplicate ids,
  non-object bodies/assets. Rewrite runs now also get asset rules (intended).
- Every flag/mode quoted in docs exists in v0.1.0's cli.py (`verify --out-dir`,
  `register --mode drift|all|ids`, `--ids-file`, `--dryrun`, verify by flags with no
  `--config`, `audit --dir --expect`). `--mode all --dryrun` returns before any fetch/write.
- `ids-from-urls` over `data/urls_list.txt`: 102,416 lines → 102,416 unique ids; fixed and
  unfixed `https:/` give the same id (url_to_item_id's `lstrip("/")`), matching item_create.
- Encoder/decoder test: item links are built with `encode_url_for_gdal` in item_create,
  item_rewrite, collection_patch, so the round trip pins the real encoder.
- stacs canonicalisation (null strip, RFC 8785, sha256, `links` dropped) matches CLAUDE.md;
  stacs `research/pgstac_round_trip.md` exists at v0.1.0 and on main; stacs repo is public;
  stac_airphoto_bc#42 and #49 titles match their citations.
- No tracked file outside planning/, logs/, NEWS history and the accepted research body
  references the deleted scripts or register_manifest functions, except item 1 below.

## Findings

- **[stale-doc]** `index.html:3551`, `README.html:765` — The GitHub Pages landing page
  (Pages serves `main` `/`, https://www.newgraphenvironment.com/stac_dem_bc/) still says
  registration is `scripts/catalogue_register.sh`, which this diff deletes. README.Rmd and
  README.md were edited but `index.html` (rendered by README.Rmd's `build` chunk,
  `output_file = "index.html"`) was not. Each of the last three README commits
  (f0256cd, c2599a5, aefb334) updated index.html alongside README.md, so this breaks that
  practice and leaves the public page naming a script that no longer exists. The task_plan
  note "`rmarkdown::render` emits only README.html here" misses the second render call in
  that chunk.

- **[stale-doc]** `CLAUDE.md:54-55` ("`stacs audit` ... run over every fetched body before
  anything reaches pgstac") and `scripts/README.md`, the "Set equality has one blind spot"
  paragraph ("`register` runs it over every fetched body before anything reaches the
  database"). Both are false against stacs v0.1.0. In `register.py` `_run`, `audit_items`
  runs only over `todo_paths`. In `drift` that is missing ∪ changed. In `ids` it is the
  requested ids. A drift with nothing to send returns "already in sync" before the audit,
  and `verify` never audits. So a stale `image`-keyed or wrong-collection body that is already
  registered with that same body is never reported by `verify` or `drift`. That is the
  mixed catalogue the sentence says is caught. The old script had the same todo-only scope,
  so this is an inherited false claim, but this diff rewrites both sentences. Say "over every
  body it is about to send".

- **[stale-doc]** `NEWS.md` `## Unreleased`, the bullets after the new ones (~lines 37-75).
  The newest tag is v2.0.0, so the earlier #42/#45 bullets in this section were never
  released. They describe `scripts/catalogue_register.sh`, `STAC_REQUIRE_ASSET` /
  `STAC_FORBID_ASSET`, `register_manifest.py audit-items` output, and
  `--verify/--drift/--all`. This diff deletes all of them, so the next release's notes
  would announce as new a script, env vars and a subcommand that do not exist in that
  release. The section also contradicts itself: "#42's own-bucket test is gone" sits above
  "asset rules now apply only when the collection id, the bucket ... or any published item
  link is this repo's". The "past releases are not rewritten" acceptance does not cover
  this section, because none of it has shipped. Fold those bullets into the stacs entry, or
  restate them as stacs behaviour.

- **[fragile]** `scripts/README.md:229-231`, the subset recipe `ids-from-urls --urls-file
  data/urls_new.txt > ids.txt` then `stacs register --mode ids --ids-file ids.txt`. stacs
  refuses the whole run if any requested id has no item link in the published
  collection.json ("N requested id(s) have no item link", raised before any write). But
  `urls_new.txt` lists every new source URL. It includes URLs that `item_create` skipped as
  unreadable, which the workflow tolerates with a shortfall warning, so they never became
  items. Any month with a shortfall therefore makes the documented recipe fail outright.
  The failure is loud and writes nothing, so nothing is damaged. Still, the doc presents
  the recipe as the way to register a month's new items, and `--mode drift` is the one that
  works. Either note the limitation, or filter ids against the published collection first.

- **[stale-doc, planning]** `planning/active/task_plan.md:25`. The checked Phase 2 item
  says both steps pass `--collection-id "$COLLECTION"`. The implementation deliberately
  drops it, and the workflow comment explains why. `task_plan.md:39` likewise says
  research/pgstac_round_trip.md became a "short pointer", while the implementation keeps
  the original body under a continuation header. The checkboxes now describe a design that
  was not built.
