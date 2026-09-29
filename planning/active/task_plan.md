# Task: catalogue_register.sh --verify/--drift cannot see an item whose content changed (#45)

`catalogue_register.sh --verify` and `--drift` compare **id sets** only (`register_manifest.py diff`). An item that is registered but whose published body has since changed is invisible to both. `--verify` reports `IN SYNC` and `--drift` has nothing to do, while the API serves the old properties and `file:checksum`.

That is the normal state after a rebuild in any collection that rewrites existing ids. stac_airphoto_bc#33 hit it: #23 there rewrote all 10,100 items, so its docs now prescribe `--all` for every registration, because nothing can say whether one is needed.

A one-item spot-check in the docs was tried and removed in review. It sampled a fixed item unrelated to what a run changed, and `null == null` made it pass when neither body could be fetched.

## Design (approved 2026-09-28)

- **One digest function:** `body_digest(doc)` returns the sha256 of the canonical JSON with
  `links` removed.
- **One Python fetcher** replaces the shell `fetch_one.sh`/`xargs` loop for **every** mode:
  `register_manifest.py fetch-bodies`. It uses a ThreadPoolExecutor and
  `urllib.request.urlopen`, which handles both `https://` and the tests' `file://`. It retries
  3 times, writes each body to `<md5(url)>.json` (same naming as today) via a `.part` file and
  an atomic rename, and lists failures in `failed.txt`. The shell's `N_FETCHED == N_URLS`
  guard stays as the gate. Keeping two fetchers for the same bodies would let them drift
  apart; one fetcher also makes `--all` about 10× faster.
- **`--verify` / `--drift` fetch every published body first.** Then `register_manifest.py
  diff` pages full bodies from the API (the same keyset loop as `ids_registered`, generalised
  to return `{id: digest}`) and writes `missing`, `orphaned` and a new **`changed`** list
  (the id is in both sets but the digests differ). Any published body that is absent or
  unreadable raises an error; it never counts as unchanged.
- **`--verify`** reports all three directions. IN SYNC only when all three are empty.
- **`--drift`** registers `missing ∪ changed` straight from the bodies already on disk, with
  no second fetch. The audit and `item_register.sh` are fed an explicit list of paths for the
  todo ids, not `find` over the whole fetch directory. The ssh probe moves ahead of the fetch
  in `--drift`, so a dead host doesn't cost a 5-minute fetch first.
- **Collection body:** the same digest check. If the published `collection.json` differs from
  the API's copy, `--verify` reports it and `--drift` re-upserts the collection even when no
  item is in the todo list. It's one GET, and it's the same defect: a version bump with no
  item changes is invisible today.
- **The post-register verify checks content:** `verify-serving` also compares the API body's
  digest with the fetched file for every registered id. If pgstac ever normalised a field,
  `--drift` would re-register the same items every month and never converge. This makes such
  a run fail on its first pass rather than loop without anyone noticing.
- **No opt-out flag.** An id-only mode would be a guard that fails toward "pass".
  `--verify` goes from about 3m40s to about 10 min; the docs say so.

## Phase 1: Tests first (red)
- [x] Unit tests, `test_register_manifest.py`: `body_digest` ignores `links` and key order;
  it changes on any property, asset or geometry change
- [x] Unit test: the content diff returns `changed` for an id in both sets with different
  bodies, and treats a missing or unreadable published body as an error, not as unchanged
- [x] Unit test: `fetch-bodies` writes `<md5(url)>.json` for `file://` hrefs and records
  failures (retries exhausted) in `failed.txt`
- [x] Offline e2e, `test_catalogue_register.py`: a tiny stub API (a local `http.server` on an
  ephemeral port, answering `/search` and `/collections/<id>`) serving one stale body.
  `--verify` exits 1 naming it as changed; `--drift --dryrun` lists it in the todo list;
  an in-sync fixture prints IN SYNC
- [x] Run the new tests with the fix temporarily reverted to confirm they fail (red)

## Phase 2: Python core (`scripts/register_manifest.py`)
- [ ] `body_digest()`
- [ ] Generalise the paging in `ids_registered` into `bodies_registered()` → `{id: digest}`;
  `ids_registered` stays as a thin wrapper, or is removed if nothing else calls it
- [ ] `fetch-bodies` subcommand (Python fetcher, `file://` + `https://`, retries, atomic write)
- [ ] `diff` gains `--fetch-dir` + `--changed-out`; `ids_diff` is extended to
  `(missing, orphaned, changed)`
- [ ] Collection digest compare (`collection-changed` subcommand, prints `same`/`changed` on
  stdout, the same pattern as `same-bucket`)
- [ ] `verify-serving` checks content against `--fetch-dir`

## Phase 3: Orchestrator (`scripts/catalogue_register.sh`)
- [ ] Replace the `fetch_one.sh`/`xargs` loop with `fetch-bodies`; keep the count guard and
  the failure report
- [ ] `--verify`/`--drift`: fetch all published bodies → content diff → report / todo =
  missing ∪ changed
- [ ] Register from an explicit list of todo paths (audit + `item_register.sh`), not `find`
  over the whole directory
- [ ] Move the ssh probe before the fetch for `--drift` (non-dryrun)
- [ ] Re-upsert the collection when its body changed
- [ ] Update the header comment (modes, "set equality AND content")

## Phase 4: Docs and full-scale acceptance
- [ ] `scripts/README.md` (verify section, timing, fetch speed), NEWS.md entry,
  CLAUDE.md "Never verify a registration by a count" paragraph: ids AND content
- [ ] Run `--verify` against the live catalogue: expect 102,460 published, 0 missing,
  0 orphaned, **0 changed**. Record the wall time. This is the full-population check that the
  2,000-item sample stands in for.
- [ ] Run `--drift --dryrun` live: expect nothing to register

## Phase 5: Close out
- [ ] `/code-check` on every commit, with three review rounds
- [ ] File a stac_airphoto_bc issue: `--drift` now detects rewritten items, so its docs can
  stop prescribing `--all` every time
- [ ] `/planning-archive`, `/gh-pr-push`

## Validation

- [ ] Tests pass
- [ ] `/code-check` clean on each commit
- [ ] PWF checkboxes match landed work
- [ ] `/planning-archive` on completion
