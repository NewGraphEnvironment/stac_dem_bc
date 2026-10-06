# Progress — Adopt stacs for registration and verification (#49)

## Session 2026-10-06

- Plan-mode exploration — phases approved by user ("go all phases")
- Created branch `49-adopt-stacs-for-registration-and-verific` off main
- Scaffolded PWF baseline from issue #49 with approved phases
- Next: start Phase 1

### Phase 1
- stacs 0.1.0 installed into `.venv` (`uv pip install`, built from d4934dd = tag v0.1.0)
- `stacs.toml` committed; `tests/test_stacs_config.py` pins it to the modules and runs the installed CLI
- Mutation: deleting `forbid` from stacs.toml turns 4 tests red (pin + three audit behaviours)
- `importorskip("stacs")` replaced with a hard import — a skip would hide a CI that never installed it
- /code-check: 3 rounds. R1: vacuous secrets test → replaced. R2 (inside R1's fix): `match="password"` matched tmp_path's name → match the refusal. R3 (enumeration, 23 asserts, 40+ mutations): stale fixtures always sorted last, so a last-item-only audit passed → stale item in the middle + counts; also whole-pin regex, every HOST=/DB= assignment. Ended by re-running the full mutation set (34) on the fixed file: every one red; `--expect` gained its own test.
- Plan review (Plan agent) → `review-plan.md`, dispositions inline

### Phases 2–4
- Workflow: both audit steps → `stacs audit --config stacs.toml`; `--collection-id` dropped (declared id is the reference); rules now on backfill runs too
- Deleted catalogue_register.sh, item_register.sh, collection_register.sh, test_catalogue_register.py; register_manifest.py → ids-from-urls (+ fix_url, see findings)
- Docs: scripts/README, CLAUDE.md (above marker), README.Rmd/.md/.html + index.html, NEWS (Unreleased rewritten: its #42/#45 bullets described never-shipped tooling), research continuation header
- /code-check (combined diff, committed per phase): R1 5 stale-doc findings (landing page, audit scope overstated, NEWS, ids recipe caveat, plan text) → fixed. R2 2 wording errors, one inside a fix → ended by enumerating every added claim about URL form / dryrun (5 lines; 3 corrected)
