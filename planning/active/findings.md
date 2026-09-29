# Findings — catalogue_register.sh --verify/--drift cannot see an item whose content changed (#45)

## Issue context

## Problem

`catalogue_register.sh --verify` and `--drift` compare **id sets** only (`register_manifest.py diff`). An item that is registered but whose published body has since changed is invisible to both. `--verify` reports `IN SYNC` and `--drift` has nothing to do, while the API serves the old properties and `file:checksum`.

That is the normal state after a rebuild in any collection that rewrites existing ids. stac_airphoto_bc#33 hit it: #23 there rewrote all 10,100 items, so its docs now prescribe `--all` for every registration, because nothing can say whether one is needed.

A one-item spot-check in the docs was tried and removed in review. It sampled a fixed item unrelated to what a run changed, and `null == null` made it pass when neither body could be fetched.

## Ask

A content comparison inside the orchestrator: `--verify` (or a separate mode) reports ids whose registered body differs from the published one, and `--drift` registers them. Candidates: compare a hash of each fetched body with the stored item, or compare `properties.updated` / an asset `file:checksum` where the collection carries one. Any of these has to fail, not pass, when a body cannot be fetched.

## Plan-mode exploration (2026-09-28, read-only probes)

- **Registered bodies match published bodies exactly once `links` is dropped.** On 2,000
  items (all carrying `dsm`), `sha256(json.dumps(doc − links, sort_keys))` is identical for
  the S3 body and the API body: 0 of 2,000 differ. The geometry comes back at full float
  precision and the `datetime` string is unchanged. The collection body matches too, again
  ignoring `links`. So the comparison can hash the whole body minus `links`. It doesn't need
  proxies like `properties.updated` (items don't carry one) or `file:checksum` (not present).
- **The cost fits.** An API page of 10,000 full bodies takes 29 s (18 MB), so all 102,460
  take about 5.5 min. Fetching from S3 in Python with 32 threads took 4.9 s for 2,000, so
  about 4–5 min at full scale. The existing shell fetch (`xargs` + one `curl` per item) is
  documented at about 45 min for a full `--all` (`scripts/README.md:236`). That is too slow
  for a verify step that now has to read every published body.
- **Offline test harness:** `tests/test_catalogue_register.py` publishes a `file://`
  bucket, stubs `curl` and `ssh`, and points `STAC_API` at `127.0.0.1:9`. Today no test
  runs `--verify` or `--drift` end to end.

Probe script: one-off, in the session scratchpad; the method is the 2,000-item
API page (`POST /search`, `limit: 2000`, full bodies) against a 32-thread S3
GET of the same ids, compared by `sha256(json.dumps(doc - links, sort_keys=True))`.

## Errors Encountered

| Error | Resolution |
|-------|------------|
