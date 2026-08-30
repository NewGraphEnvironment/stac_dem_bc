# Review round 3 — narrow pass on `catalogue_register.sh` + `register_manifest.py` (#27)

Scoped to the two files as instructed. Everything below was probed — against the live
API, the live 19.4 MB `collection.json` (102,460 item links, fetched today), the live
`data/urls_list.txt`, and bash 3.2.57 — not inferred from reading. Nothing already
listed in rounds 1 or 2 is re-reported as new.

## The mechanism

Rounds 1 and 2 each found a bug in the same comparison. That is not a coincidence of
bug-hunting: **it is the only comparison left in the whole pipeline where the
expectation and the artifact come from different producers.** Every count in the
chain was traced:

| # | count | derived from | compared against | producer-shared? |
|---|-------|--------------|------------------|------------------|
| 1 | `N_PUBLISHED` (89) | lines of `published.txt` = one per item **link** | only `-eq 0` and the IN SYNC message | n/a |
| 2 | `N_TODO` (134) | lines of `todo.txt` = unique **ids** | `N_FETCHED` | **no** |
| 3 | `N_ORPHANED` (148) | lines of `orphaned.txt` | `-gt 0` | yes (Python set → one line each) |
| 4 | `N_FETCHED` (248) | md5-named files = unique **URLs** | `N_TODO` | **no** |
| 5 | `N_FAILED` (249) | lines of `failed.txt` | diagnostic only | n/a |
| 6 | `EXPECTED` (item_register:61) | stdin lines | `WRITTEN` | yes — same `find` output |
| 7 | `WRITTEN` (item_register:75) | `ndjson_write` return | `EXPECTED` | yes — same path list |
| 8 | remote `n` (item_register:111) | `wc -l` of received | `EXPECTED` | yes — same file |
| 9 | `verify-serving` | set difference | set | yes |

Rows 6–9 are structural: the count and the thing counted are produced by the same
step, so they cannot disagree. **Row 2 vs row 4 is the only pair that can**, and both
shipped bugs landed on it:

- Round 1's `limit` bug was a different defect, but round 1's own "Checked and
  correct" section records this exact pair and dismisses it on the evidence that the
  live `collection.json` has 102,460 unique hrefs and 102,460 unique ids.
- Round 2's `--ids-file` bug **is** this pair, arriving from the other side.

Both fixes were applied to the *inputs* (`sort -u` twice, a Python set once). The
comparison itself is unchanged. So the honest answer to "is the invariant enforced or
merely satisfied": **merely satisfied, by three independent dedupe mechanisms plus one
property of the published data that nothing asserts.**

The same shape — *one fact derived twice by two independent code paths, never
reconciled* — accounts for all three findings below. They are not three more instances
of a count bug; they are the same defect in three different facts (how many, which id,
which collection).

Fix for row 2/4 that makes it structural, one line: derive the expectation from
`urls.txt` — the same artifact the fetcher consumes — after the `cut` at line 181:

```bash
cut -f2 "$WORK/hrefs.tsv" | sort -u > "$WORK/urls.txt"
N_EXPECTED=$(count_lines "$WORK/urls.txt")     # then compare N_FETCHED to this
```

Nothing is lost by it: `hrefs-published` already raises when a wanted id has no link
(register_manifest.py:281-285), and `verify-serving` still checks the *ids* against
the live API at the end. The guard then asks the question it is actually for — "did
every URL I tried land?" — instead of a proxy for it.

## Findings

- **[fragile] scripts/catalogue_register.sh:134 + 248-252, with
  scripts/register_manifest.py:269-287 — the fetch guard compares a count of **ids**
  against a count of **URLs**, and the only thing making those equal is that the
  published collection happens to map one id to exactly one href.**
  `hrefs-published` filters `links` by set membership (`i in wanted`) and emits **one
  row per matching link**, not one per wanted id. It raises on `wanted - found`
  (an id with no link) and is silent on the opposite direction (an id with several
  links). Reproduced with the real script against a 3-link collection:

  ```
  todo.txt: 2 ids            hrefs.tsv: 3 rows -> 3 distinct URLs -> 3 md5 files
  N_TODO=2, N_FETCHED=3  ->  "ERROR: fetched 3 of 2 — nothing sent to the database"
                             failed.txt empty, fetch_stderr.txt empty, no explanation
  ```

  This is round 2's failure verbatim, in a form that **no amount of deduping
  `todo.txt` can reach** — the duplication is on the href side. It is also the one
  case where the guard can fire in the `>` direction, which the error text
  ("fetched N of M") does not read as.
  Measured today the trigger is absent: 102,460 links, 102,460 unique hrefs, 102,460
  unique decoded ids, and the only percent-escape in the entire href set is `%20`.
  What upholds it is three unasserted properties: `PATH_S3_STAC` is a constant, so
  every href for an id is identical; `item_create.py:381` normalises **both** sides of
  its dedupe through `encode_url_for_gdal`, so a raw-space link and its encoded
  successor cannot both survive; and `unquote` happens to invert that encoding on the
  current charset. A bucket/prefix change, or any second writer of item links that
  encodes differently, breaks all of it at once and the symptom is a
  102,460-item fetch discarded at the last step.
  (Not a re-report of round 2: that finding was `--ids-file` line duplication and was
  fixed. This is the href side of the same comparison, still open.)

- **[bug] scripts/catalogue_register.sh:39, 42, 123-131, 267, 285 — `$COLLECTION_ID`
  and the published `collection.json` are two independent statements of *which
  collection this is*, never reconciled, and the API answers a wrong collection id
  with a silent empty set.**
  The registered side is enumerated with `--collection-id "$COLLECTION_ID"` (env,
  default `stac-dem-bc`); the published side comes from `$BUCKET_URL/collection.json`
  (env, default the prod bucket); and `collection_register.sh:77` takes the id from
  the **file**, ignoring `$COLLECTION_ID` entirely. Nothing compares
  `json.load(collection.json)["id"]` to `$COLLECTION_ID`.
  Probed live — a `/search` for a collection that does not exist returns **200, zero
  features, no `next` link**, which `ids_registered` returns as `[]` with no error:

  ```
  POST /search {"collections":["stac-dem-bc-typo"], ...}  ->  200, feats 0, links [root,self]
  ```

  So with `STAC_COLLECTION` set and `STAC_BUCKET_URL` left alone:
  `--verify` prints `DRIFT: 102,460 published item(s) are not registered` and exits 1;
  `--drift` fetches and re-registers all 102,460 items (~an hour), then passes
  `verify-serving` — which does **not** scope by collection, so it cannot detect the
  mismatch — and prints `DONE: 102,460 item(s) registered to <the-id-that-was-never-used>`.
  Every subsequent run repeats it.
  Reachable today with no code change, and the header invites it: the `Env:` block at
  lines 22-27 documents `STAC_COLLECTION` and **does not document `STAC_BUCKET_URL`**,
  so an operator reading the file learns exactly one of the two knobs that have to
  move together. Fix: assert the file's `id` equals `$COLLECTION_ID` right after the
  fetch (or derive `COLLECTION_ID` from the file and drop the env default), and
  document `STAC_BUCKET_URL`.

- **[fragile] scripts/register_manifest.py:82-91 vs scripts/stac_utils.py:405-415 —
  the item id is derived twice by non-inverse functions; a literal `%` in a source
  filename would produce a permanently unregisterable, permanently "missing" item.**
  The writer builds the href with `encode_url_for_gdal`, which encodes **spaces only**
  (`url.replace(" ", "%20")`). The reader recovers the id with
  `urllib.parse.unquote`, which decodes **every** escape. Those are inverses only
  while no id contains a literal `%`. For a source basename `x%28y.tif`:
  id = `x%28y` (`url_to_item_id` does no decoding), href = `.../x%28y.json`,
  `_href_to_id` → `x(y`. The published set then names an id the API will never serve.
  `--verify` reports it as missing **and** its true id as orphaned, forever; `--drift`
  fetches the href, registers `x%28y`, then fails `verify-serving` on `x(y` and exits
  1 after a successful upsert — the fail-toward-abort shape, repeating every month
  with no state that could ever clear it.
  Not live: `data/urls_list.txt` contains zero `%` (basename charset is
  `[ _.()0-9a-z]`, including 90 names with a literal space and parentheses), and I
  confirmed end-to-end that the decoded form is the true id — the S3 object at
  `...2018%20(2).json` carries `"id": "...2018 (2)"` and the API serves that exact
  string. The asymmetry is a latent writer/reader disagreement inside one repo, and
  the source names come from a vendor delivery nobody controls (#8 is already about
  the parentheses in them). Fix: mirror the writer — `href.rsplit("/",1)[-1].replace("%20", " ")`
  — or take the id from the fetched item JSON rather than from its filename.

- **[fragile] scripts/catalogue_register.sh:222-228 — the fetch worker's `mv` is
  unchecked and `exit 0` is unconditional, so a failed rename reports success with no
  file and no entry in `failed.txt`.**
  ```bash
  if curl -sfL --max-time 60 "$url" -o "$out.part" && [ -s "$out.part" ]; then
    mv "$out.part" "$out"
    exit 0
  fi
  ```
  The worker has no `set -e`. If `mv` fails — ENOSPC on the mktemp filesystem partway
  through a 102,460-file `--all` fetch (~500 MB of item JSON), or the `.part` already
  consumed by a concurrent worker on a duplicate URL — the worker exits 0, the file is
  absent, and nothing is appended to `failed.txt`. The run then aborts at line 252
  with `fetched N-1 of N ... Failed URLs: 0`, an empty `head -5`, and (mv's stderr does
  reach `fetch_stderr.txt`, so the block added in round 1 would print something —
  but the headline number and the failed-URL count still contradict each other).
  This is the precise diagnostic that round 2 named as "actively misleading", reachable
  from a second cause. Fix: `mv "$out.part" "$out" && exit 0`, letting the retry loop
  or the `failed.txt` append handle it.

## Checked and correct — do not re-derive

- **`count_lines` on bash 3.2.57**, all six cases, under `set -euo pipefail`:
  `empty=0, two=2, no-trailing-newline=2, single-blank-line=1, missing=0, directory=0`,
  script survives. Confirmed no state leaks between calls (`local n` is fresh each
  invocation, so the `|| n="${n:-0}"` fallback cannot inherit a previous file's count):
  `two -> 2, missing -> 0, empty -> 0` in one sequence.
- **The `RC` accumulator and both `--verify` exits.** Re-traced; round 2's conclusion
  holds. `head -5 file | sed` cannot trip pipefail (head is the pipeline *head*,
  reading a file, and exits 0).
- **`_post` retry semantics.** It does retry 4xx, which is wasteful (2 s + 4 s of
  sleeps on a request that will never succeed) but harmless — every call is a
  read-only `POST /search`. `raise_for_status()` inside the `try` is correct, and so
  is having `resp.json()` inside it: with `requests` 2.34.2 (installed),
  `requests.exceptions.JSONDecodeError` subclasses `RequestException`, so a 200
  carrying an HTML error page is retried rather than escaping as a bare `ValueError`
  (verified `issubclass(...) is True`). Only note: the `RuntimeError` message carries
  the exception, not the response body, so a stac-fastapi 400 `detail` is lost.
- **Live API paging contract, re-probed today.** `numberMatched: null`; the `next`
  link carries `body.token = "next:stac-dem-bc:<last id>"` and `method: POST`; the
  body-merge at register_manifest.py:148 is correct. Unchanged since round 2.
- **`sort -u` collation.** Checked the specific hazard that a non-C locale can collapse
  strings differing only in punctuation — ids here contain spaces, parens, dots,
  hyphens and underscores. Ran the real 102,460 ids through GNU sort 9.11 under
  `en_US.UTF-8` and under `LC_ALL=C`, and through macOS `/usr/bin/sort`: **102,460 in
  every case**, and a hand-built adversarial pair (`bc_x (2)` / `bc_x2`) does not
  collapse either. `sort -u` is not silently dropping ids.
- **`xargs -I {}` at scale.** Max href length is 106 chars, so no `-I` line-length
  ceiling is approached; `-I` splits on newlines only, so the 90 hrefs containing
  `%20` are safe, and the live href charset (`%()-./0-9:_a-z`) contains no quote,
  backslash or raw space that could trip xargs' own quote processing.
- **`printf '%s\n' "$url" >> "$dir/../failed.txt"`** resolves to `$WORK/failed.txt`
  (`$dir` is `$WORK/items`). One short append per worker, O_APPEND, far under
  `PIPE_BUF` — no interleaving. `md5sum`/`md5 -q` branch is correct on both platforms;
  an md5 collision across 102 k URLs is not a real risk.
- **`--drift` vs `--all`.** They genuinely converge at `todo.txt` and share every
  downstream step, so the small case does exercise the large one. The only real
  divergence is coverage in the other direction: `--all` never calls `ids_registered`,
  so the paging/token-raise path is exercised only by `--drift`/`--verify`. Not a
  defect — worth knowing that an `--all` recovery cannot report drift.
- **`links_encode` (collection_patch.py:75-97) cannot manufacture the duplicate-id
  condition** in finding 1: it rewrites hrefs in place and asserts the item-link count
  is preserved, and two links collapsing to an *identical* href is self-cancelling in
  the guard (one md5 file, one `sort -u` id). Only *distinct* hrefs decoding to one id
  break it.

## One asymmetry, not a finding

Round 2 asked for `[ -f "$WORK/orphaned.txt" ]` because `count_lines` returns 0 for a
missing file. That guard was added at line 144 — for `orphaned.txt` only. `todo.txt`
is read by the identical helper twelve lines later (134) with the identical exposure,
and did not get one. Neither is reachable (`register_manifest.py diff` writes both
unconditionally, and `set -e` kills the script if the call fails), so this is not a
bug. It is worth naming as the tell for the pattern above: **each round's fix has
landed on the instance the reviewer pointed at, not on the property.** The three
findings above are what is left when you fix the property instead.
