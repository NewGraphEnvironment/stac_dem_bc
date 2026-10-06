# Review round 2 — staged phase 1 of #49

Scope: `git diff --cached` (update.yml, environment.yml, stacs.toml,
tests/test_stacs_config.py, planning files). Read against the installed stacs
(`.venv`, direct_url commit d4934dd = tag v0.1.0).

## Findings

- **[fragile]** tests/test_stacs_config.py:89 (`test_the_password_is_named_never_given`)
  — `pytest.raises(ConfigError, match="password")` is satisfied by the **path**, not
  by the refusal. stacs puts `{path}` in every ConfigError message, and pytest names
  the tmp dir after the test (`.../test_the_password_is_named_nev0/stacs.toml`), so
  "password" is always in the message whatever the error was. Measured in a staged
  snapshot copy: replacing the inserted line with `foo = 1` (unknown key, not a
  password) still passes; replacing it with `password = x` (unquoted — a TOML decode
  error, "cannot read config ...") also passes. The `raises(ConfigError)` part still
  carries the guard (a stacs that accepted `password` would not raise, and the test
  would go red), so this is not vacuous today — but the `match` asserts nothing, and
  a future edit that breaks the TOML in the copy would pass for the wrong reason.
  Fix: match on the refusal itself, e.g.
  `match=r"unknown key\(s\) in \[transport\]: password"`.

## Checked and sound

- capsys does capture what stacs prints: `cmd_audit` uses `print(..., file=sys.stderr)`,
  resolving `sys.stderr` at call time; `capsys.readouterr()` before the call clears
  prior output. All audit lines go to stderr, which is what `_audit` reads.
- Failure tests assert `returncode == 1`; a ConfigError/argparse path returns 2 or
  raises SystemExit, so a config breakage cannot pass as "audit caught it".
- Mutation table (staged snapshot copy via `git checkout-index`, never the repo):
  drop `forbid` / `forbid = []` → 4 red; drop `require` → 2 red; `collection_id =
  "stac-dem-bc"` → 2 red; change `[transport] host` → 1 red; change unregister
  HOST default → 1 red; bump either install pin alone → 1 red. Restored → 13 pass.
- `test_the_unregister_script_targets_the_same_host_and_db` matches the exact
  `HOST="${STAC_HOST:-...}"` / `DB="${STAC_DB:-...}"` lines in the staged script.
- CI: update.yml runs `pytest tests/ -q` after the install step, on Python 3.12
  (stacs needs >=3.11); stacs repo is PUBLIC, so the git+https install needs no token.
- The comment command `stacs register --config stacs.toml --host ... --mode drift`
  uses flags/modes v0.1.0 actually has.

## Note (not a defect today)

- `test_the_pinned_stacs_is_the_one_installed` compares `stacs.__version__`, which
  is `importlib.metadata.version("stacs")` — the pyproject version, not the tag.
  stacs main is one commit past v0.1.0 (CLAUDE.md only) and still says 0.1.0, so an
  editable/main install would pass this test as "the pinned one". Harmless while
  post-tag commits are docs-only; if it needs to mean "installed from the tag", read
  `direct_url.json`'s `requested_revision`/`commit_id` instead.
