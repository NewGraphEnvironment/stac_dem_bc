# Plan review — #49 (Plan agent, 2026-10-06; returned in-reply, written here by the parent)

Blocker
- B1: registering stac-airphoto-bc from this repo no longer works — with --config the
  declared require=dem refuses its items; without, transport keys (env_file, workdir,
  pypgstac…) are config-only. Decide: airphoto gets its own stacs.toml in its repo.

Acceptance
- A1: `register --dryrun` returns before the API/ssh probe (register.py:434, :500), so it
  does not exercise transport. Use a non-dryrun drift after verify is IN SYNC (probes,
  then "nothing to register", no write). `load collection` of the identical body would
  exercise remote_script but is a write.

Gap
- G1: stdin/pipe cannot feed --ids-file (Path.is_file). Use a file.
- G2: unlisted references — update.yml:15-19, :311; scripts/README :129, :186 prereqs,
  :204, :236 FETCH_JOBS; CLAUDE.md several incl. :121-123 (blocked on stacs#1);
  test_item_migrate docstrings; test_asset_key:208-211 message.
- G3: host notes (root-only rtj#193, MagicDNS expiry rtj#208, reserved-IP fallback) lived
  only in collection_register.sh:27-35 and catalogue_register.sh:375.
- G4: collection_unregister.sh keeps its own STAC_HOST/STAC_DB defaults — untested copy.
- G5: test_stacs_config locates the console script via sys.executable; call
  stacs.cli.main in-process instead.

Assumption
- S1: rewrite runs now always enforce require=dem/forbid=image; contradicts update.yml
  :353-361 rationale and item_backfill DEM_KEYS comment.
- S2: --collection-id "$COLLECTION" keeps the id from the data; dropping it makes
  stacs.toml the single source.
- S3: stacs/research/README.md cites this repo's pgstac_round_trip.md as the original
  measurement it continues — keep content, add a pointer header.
- S4: v0.1.0 is a mutable tag (optional SHA pin).

Scope
- SC1: fix_url in ids-from-urls is unplanned — record in findings + NEWS.

Ordering
- O1: run the Phase 5 live check before the PR / merge.

## Disposition
- B1: accepted. Each catalogue declares itself in its own repo; README shows a flags-only
  `verify` for another collection (no transport needed) and says register needs that
  repo's stacs.toml. Follow-up issue for stac_airphoto_bc if none exists.
- A1: accepted. Phase 5 = verify, then non-dryrun drift only if IN SYNC (no write). The
  remote load path is not exercised by this issue; reported to the user.
- G1: already a file in the recipe and docstring.
- G2: fixed across files (workflow comments in Phase 2).
- G3: notes moved into stacs.toml comments.
- G4: test pins collection_unregister.sh defaults to [transport].
- G5: in-process stacs.cli.main + capsys.
- S1: workflow and item_backfill comments say so.
- S2: drop --collection-id; stacs.toml is the single source (pinned to the module).
- S3: original content restored with a continuation header.
- S4: keep the tag, as the issue specifies.
- SC1: in findings already; NEWS too.
- O1: Phase 5 runs before the PR.
