# Review round 4 — final narrow pass on the round-3 fixes (#27)

Scope: commit `c1ee6f1` only (`scripts/catalogue_register.sh`,
`scripts/register_manifest.py`, `tests/test_register_manifest.py`), read in the
context of both files in full. Nothing from rounds 1–3 is re-litigated.

## Clean

All four fixes hold under adversarial probing. Evidence below, so this pass is
checkable rather than an assertion.

### 1. The structural count fix — the invariant is now genuinely structural

`N_URLS = |distinct hrefs among links whose id is in todo.txt|`, and
`href -> id` is a *function* (`_href_to_id` reads only the basename), so distinct
ids necessarily have distinct hrefs. Therefore **`N_URLS >= N_TODO` always**, and
`N_URLS > N_TODO` iff some single id carries two or more distinct hrefs — which is
exactly the malformed-collection condition the guard names. The under-count
direction the review brief asked about is not merely absent from live data, it is
unreachable: two distinct ids cannot collapse onto one URL because the id is
derived from the URL. So the new `N_URLS != N_TODO` check cannot false-abort a
healthy run.

Walked all three modes: `--all` (`sort -u published.txt`), `--ids-file`
(`grep -v blank | sort -u`, plus `hrefs-published`'s `wanted - found` SystemExit
covering the missing-link direction), `--drift` (`ids_diff` returns
`sorted(set - set)`). Each yields unique ids into `todo.txt`; each id resolves to
one href in the live collection.

Live measurement against `https://stac-dem-bc.s3.amazonaws.com/collection.json`
(19,434,562 bytes, fetched this session):

```
item links                        102,460
distinct hrefs                    102,460
distinct decoded ids              102,460
duplicate hrefs                         0
duplicate ids                           0
ids with >1 distinct href               0
```

So the guard fires on nothing today, and it cannot fire on anything healthy.

`sort -u` on `urls.txt` also closes, as a side effect, the concurrent-`.part`
collision round 3 raised: two workers can no longer be handed the same URL, so
they can no longer race on the same `$out.part`.

md5 collision on two different URLs would give `N_FETCHED < N_URLS` and abort —
the safe direction, and not a realistic risk at 102 k.

### 2. Collection-id reconciliation — correct and correctly placed

Runs at line 97-105: after the 19 MB `curl` and the `ids-published` parse (both
seconds), and **before** the ~200 s keyset enumeration, before the ssh probe,
before the ~1 h fetch, and before `--dryrun` exits. It gates every mode including
`--verify`.

Cannot false-positive: verified `collection.json["id"] == "stac-dem-bc"`, matching
the `STAC_COLLECTION` default. If the key were absent, `.get("id","")` yields `""`,
which mismatches and aborts — fail-toward-refuse, which is right here because the
alternative is registering under an id the fetch never came from. If the `python -c`
itself failed, the bare assignment's exit status propagates under `set -e` and the
script dies rather than continuing with an empty value.

### 3. `stem.replace("%20", " ")` — verified the exact inverse, and byte-identical
    on live data

The producer is `item_create.py:187` — `encoded_item_href =
encode_url_for_gdal(item_href)` — and `encode_url_for_gdal` is
`url.replace(" ", "%20")`, spaces and nothing else. So the new decoder is the exact
inverse; `unquote` was not.

Ran both decodings over all 102,460 links and compared:

```
decode differences, unquote(stem) vs stem.replace("%20"," "):   0
stems containing '%':                                          90
escape sequences present in the entire href set:      {'%20': 90}
basename charset beyond [A-Za-z0-9._-]:      {'%': 90, '(': 90, ')': 90}
```

Parentheses are **raw** in the published hrefs, never `%28`/`%29` — which is itself
the fingerprint of a space-only encoder, and confirms independently of the source
that `unquote` was over-decoding by capability rather than by effect. All 90
percent-encoded ids decode identically under both functions, so **no id changes
published/missing status**. No silent set change shipped.

The new tests reach the defect: `unquote("100%25")` is `"100%"`, so
`test_href_to_id_is_the_exact_inverse_of_the_encoder` goes red against the old
code. It is a real guard, not decoration.

### 4. The fetch worker's `mv` check — failure is recorded, exit status is right

```
mv fails -> break (skips the retry sleep)
         -> rm -f "$out.part"
         -> printf '%s\n' "$url" >> "$dir/../failed.txt"
         -> exit 1
```

The URL lands in `failed.txt`, `N_FAILED` counts it, `N_FETCHED` is short, and the
guard at line 296 aborts with a diagnostic whose headline and failed-URL count now
agree. `break` rather than `continue` is correct: a rename failure is not
transient, and the `.part` has already been removed by the next statement. No
`set -e` is needed on this path because every branch exits explicitly.

`find -name '*.json'` does not match `$key.json.part`, so an abandoned partial can
never be counted as present.

### Verified, not re-derived

- `90 passed` (`pytest tests/ -q`), including the three tests added by this commit.
- The `--dryrun` message still prints `$N_TODO`, which the new guard has just
  proven equal to `$N_URLS` — not a stale variable.
- `cut -f2 | sort -u` cannot trip `pipefail`; `count_lines` on a `sort` output is
  exact (always trailing-newline-terminated).
- xargs quote handling, `sort -u` collation, `count_lines` edge cases, `_post`
  retry semantics and the paging contract were measured in round 3 and are
  untouched by this commit.

Three rounds of findings, one round of fixes, no new defect in the fixes.
Convergence.
