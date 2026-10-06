# Code-check round 3 — #49 phase 1 (staged diff)

Scope: `git diff --cached` only — `.github/workflows/update.yml`, `environment.yml`,
`stacs.toml`, `tests/test_stacs_config.py`. Every mutation below was run in a copy of the
staged snapshot (`git checkout-index -a --prefix=scratchpad/r3/`), one fresh copy per
mutation, with the repo's `.venv`. Mutations to stacs itself were made on a copy of the
installed package (and its dist-info) placed first on `PYTHONPATH`. Harness:
`scratchpad/mut/run.py`, `scratchpad/mut/lines.py` (line-level attribution).

Baseline on the snapshot: 13 passed.

## The mechanism behind (1) and (2)

Both assertions were accepted because **the expected value showed up in the output**,
without asking what else in the test's own setup could have put it there. In (1) the
reference was the file the value had just been read from; in (2) the matched word came
from the input path, because pytest names `tmp_path` after the test. Both are the test's
own setup satisfying the assertion. Neither was run against the defect, which is how both
survived. The general form is: **an incidental property of the setup, chosen by the test
author without noticing, is enough to make the assertion true.**

This round's finding has the same shape, but the incidental property is **position**,
not text.

## Findings

- **[severity: fragile]** tests/test_stacs_config.py:159 and :172 — every negative
  fixture places the offending item **last** in the order stacs audits it. stacs reads
  `sorted()` filenames, and `good0, good1, stale` and `good, relabelled` both sort the
  stale item to the end. `test_a_flag_cannot_loosen_the_declared_rules` has a single item.
  So the classic loop defect passes **all 13 tests**: the rule checks dedented out of the
  `for path in paths` loop in `audit_items`, so that they run once against the last document.
  I ran it (`stacs_checks_last_item_only`). That is the exact failure the module docstring
  and the line-158 docstring name ("THE failure: … one stale item among migrated ones").
  A stale item that is not last, which is most of 102k, would load. The mirror defect
  (`paths[:1]`) is caught, so only the last position is blind.
  **Fix (verified):** put the stale item in the middle,
  `[_migrated("a_good"), _published("m_stale"), _migrated("z_good")]`, and
  `[_migrated("a_good"), stale, _migrated("z_good")]` at :172. Both tests then go red
  under the last-only mutation, and all 13 stay green without it. Optionally assert
  `"1 item(s) still carry a retired asset key"`. As written, the assertions match only the
  category text, which is also produced when *other* items are flagged. Under the
  `toml_collection_id` mutation, the stale test stays green while the *good* items are
  the ones reported as naming another collection.

- **[severity: fragile, low]** tests/test_stacs_config.py:107-108 — substring presence of
  `HOST="${STAC_HOST:-root@geopro}"` does not mean that line is in effect. A later
  `HOST="root@old"` line in `collection_unregister.sh` leaves the test green
  (`sh_host_reassigned_after`). This is low risk because the script is short and has one
  assignment today. Noted because the test's docstring claims "a host move would leave the
  delete path pointing at the old machine" is caught, and only the default literal is pinned.

- **[severity: fragile, low]** tests/test_stacs_config.py:66 — `pin in text` is a
  substring test, so `stacs@v0.1.01` in one file passes (`envyml_pin_v0.1.01`). This is
  contrived. Anchoring on a terminating quote or end of line would close it. Not blocking.

Not findings, recorded for completeness:
- `--expect 3` at :152 is never shown to fire. `stacs_expect_ignored` (stacs' count check
  disabled) leaves everything green. No assert names that property, so this is not a false
  claim. The argument is just decoration.
- :57 checks `requested_revision`, not `commit_id`, so a force-moved tag would pass. The tag
  is lightweight, and it resolves to `d4934dd`, which is the installed commit. The repo is
  public, so the CI `git+https` install needs no token.
- `environment.yml` `python>=3.11` matches stacs' `Requires-Python: >=3.11` (it needs
  `tomllib`). CI pins 3.12, and `pytest tests/` in CI collects this file.

## Enumeration: every assert, the mutation that turns it red

All of these were run. "Line" is the line that pytest reported as failing.

| line | test | assert | mutation that turns it red | ran → red at | passes for another reason? |
|---|---|---|---|---|---|
| 55 | pinned_stacs_is_the_one_installed | `__version__ == "0.1.0"` | dist-info METADATA `Version: 0.2.0` | yes → 55 | no |
| 57 | " | `requested_revision == "v0.1.0"` | direct_url.json `requested_revision: main` | yes → 57 | moved tag passes (see note) |
| 66 | every_install_path_pins_the_same_tag | `"stacs@v0.1.0" in update.yml` / `environment.yml` | either pin set to `v0.2.0` | yes, each file → 66 | `v0.1.01` passes (substring) |
| 70 | collection_id_is_the_modules | toml == `collection_patch.COLLECTION_ID` | toml `stac-dem-bc`; module `x` | yes → 70 (both) | no |
| 74 | bucket_url_is_the_modules | toml == `PATH_S3_STAC` | toml bucket `x` | yes → 74 | no |
| 78 | required_asset_is_the_modules | toml == `ASSET_DEM` | toml `require="dsm"`; require dropped | yes → 78 | no |
| 83 | forbidden_assets_are_every_retired_key | `set(forbid) == set(ASSET_RENAMES)` | toml forbid dropped; `+ "dem"`; module gains `"img"` | yes → 83 | no |
| 84 | " | `ASSET_DEM not in forbid` | `ASSET_RENAMES` gains key `dem` **and** toml forbid gains `dem` (83 then holds) | yes → 84 | no |
| 91 | password_is_named_never_given | `password_env == "POSTGRES_PASSWORD"` | toml `password_env = "PGPASS"` | yes → 91 | no |
| 97 | " | `raises(ConfigError, match=r"unknown key\(s\) in \[transport\]: password$")` | stacs accepts `password` in [transport] (DID NOT RAISE); stacs rewords message; toml header `[transport]  # …` so nothing is inserted | yes → 97 (all three) | no. The tmp dir name cannot reach the `$`-anchored suffix, and a failed insert fails toward red |
| 107 | unregister_script_targets_the_same_host_and_db | `HOST="${STAC_HOST:-<toml host>}"` in sh | toml host changed; sh default changed | yes → 107 (both) | later reassignment passes (finding 2) |
| 108 | " | `DB="${STAC_DB:-<toml db>}"` in sh | toml db changed; sh default changed | yes → 108 (both) | same shape as 107 |
| 153 | audit_passes_a_migrated_population | `returncode == 0` | toml collection_id wrong; `item_migrate` no-op; forbid gains `dem`; stacs flags every item | yes → 153 | no |
| 154 | " | `"require=dem" in stderr` | toml `require` dropped (prints `require=-`); stacs reworded `rules` | yes → 154 | no |
| 161 | catches_one_stale_item_among_migrated_ones | `returncode == 1` | cmd_audit returns 0 on failure | yes → 161 | **last-only audit passes (finding 1)** |
| 162 | " | `"name another collection" in stderr` | stacs collection check disabled; label reworded | yes → 162 | also true when the *good* items are flagged (toml collection_id wrong) |
| 163 | " | `"retired asset key" in stderr` | toml forbid dropped; stacs forbid check disabled; label reworded | yes → 163 | **last-only (finding 1)** |
| 173 | catches_a_retired_key_even_in_the_right_collection | `returncode == 1` | toml forbid dropped; stacs forbid disabled; rc forced 0 | yes → 173 | **last-only (finding 1)** |
| 174 | " | `"retired asset key" in stderr` | forbidden label reworded (rc stays 1, text gone) | yes → 174 | **last-only (finding 1)** |
| 182 | a_flag_cannot_loosen_the_declared_rules | `returncode == 1` | stacs drops require+forbid when `--collection-id` given; rc forced 0 | yes → 182 | no (single item, so position is moot) |
| 183 | " | `"retired asset key" in stderr` | stacs drops forbid when `--collection-id` given; toml forbid dropped; label reworded | yes → 183 | no |
| 188 | audit_refuses_an_empty_directory | `returncode == 1` | stacs empty-check disabled; rc forced 0 | yes → 188 | no |
| 189 | " | `"no item JSONs" in stderr` | stacs reworded empty label | yes → 189 | no (tmp dir `test_audit_refuses_an_empty_di0` does not carry it) |

Row 174's mutation keeps rc 1 because, under `label_forbidden_reworded`, the forbidden
check still fires and only its text changes. So 174 is pinned independently of 173.

Every assert has a mutation that turns it red, so none is inert. The asserts that pass for
a reason other than the property they name are 161, 163, 173 and 174. They hold under an
audit that checks only the last item, because the fixtures always put the offender last.
162 also holds when the wrong items are flagged, but 153 catches that, so it is only a
weakness, not a gap.

## Verdict

One real test gap (finding 1), with a verified two-line fix. Two low-severity substring
fragilities. The staged config, the pins and the install lines are correct.
