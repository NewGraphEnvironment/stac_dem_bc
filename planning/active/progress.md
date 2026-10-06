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
