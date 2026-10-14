# Code-check round 4 (#60): the probe exports no credential

Reviewed: branch `60-update-yml-oidc-credentials-expire-an-ho` at 6f3b048, focus on round 3's
`output-env-credentials: false` on the early probe and `test_the_probe_exports_no_credential`.
Action source read at the `v4` tag (tag object ff71707 -> commit 7474bc4, "always tracks the
latest v4 release"), `src/index.ts`, `src/helpers.ts`, `src/cleanup/index.ts`, and the bundled
`dist/index.js` the action actually executes (`runs.main`), which matches src on every line below.

## Clean

No defect found in the round-3 fix. Each point checked concretely:

(a) **The probe is still a probe.** With `output-env-credentials: false`, `run()` still calls
`getIDToken` then `assumeRole` (AssumeRoleWithWebIdentity) through `retryAndBackoff`, which
rethrows after the last attempt; the `catch` calls `core.setFailed`. The flag only gates
`exportRegion`, the env half of `exportCredentials`, and the post-assume `exportAccountId`. It
cannot succeed without assuming: `use-existing-credentials` (the only early `return`) is unset,
and if `id-token: write` were lost the OIDC branch is skipped and `validateCredentials()` fails
on a runner with no credentials.

(b) **Exports nothing a later step reads.** `exportRegion` is gated by the same flag, so
`AWS_REGION`/`AWS_DEFAULT_REGION` are no longer set by the probe. Nothing between the probe and
the sync needs them: `detect_changes.R` lists via `ngr::ngr_s3_keys_get` (plain `httr::GET`, no
AWS env), collection fetches are anonymous `curl https://...`, Python steps read `https://` via
GDAL `/vsicurl/`, the commit step uses git only. The probe has no `id`, so the skipped
`aws-account-id` / `authenticated-arn` outputs are not referenced anywhere. `setSecret` masking
still runs regardless of the flag. Post-cleanup is also gated on the input being `'true'`, so it
is a no-op for the probe and harmless for the late step.

(c) **The late step exports everything the CLI needs.** It omits the input, so default `'true'`:
`AWS_DEFAULT_REGION` + `AWS_REGION` (us-west-2), `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
`AWS_SESSION_TOKEN`. `scripts/s3_sync-ci.sh` passes no `--region` and relies on the default
provider chain, which these satisfy. `exportAccountId` after export makes the first signed call
(GetCallerIdentity) with the freshly exported env; AssumeRoleWithWebIdentity is unsigned.

(d) **Floating `v4`.** The tag moves within v4 (maintenance line; v6.3.0 is current). A future
v4 flipping the default to `false` would make the late step export nothing and the sync fail
loudly ("Unable to locate credentials" on a hosted runner), i.e. fails closed. Not a silent risk.

(e) **YAML boolean.** `with:` values reach the action as strings; YAML `false` arrives as
`"false"`, `|| 'true'` applies only to the empty string, and `"false".toLowerCase() === 'true'`
is false. The test reads PyYAML's `False` via `str(...).lower()`, so `false`, `False` and
`"false"` all pass, matching the action.

(f) **`creds[0]` indexing.** Position-based, but every perturbation fails toward FAIL, not pass:
removing the probe makes `creds[0]` the late step (absent -> `"true"` != `"false"`) and also
trips `len(creds) >= 2`; inserting a credentials step ahead of the probe makes that step
`creds[0]`, which fails unless it too exports nothing; any later step set to `false` fails the
`creds[1:]` loop.

Mutation check (temp copy, not the repo): probe set to `true` -> test fails; probe line deleted
-> test fails; `"false"` quoted -> passes (correct, the action reads it identically).

Regression check: full suite in a `git archive` copy, 314 passed; the one failure
(`test_urls_scheme`) is an artefact of the copy not being a git repo (`git ls-files` errors) and
passes in the real repo (6 passed). Both credentials steps keep the same role and region; the
late step's `if:` is byte-identical to the sync's, and both carry the implicit `success()`, so a
failed re-assume skips the sync and the commit step discards the rewrite manifests as designed.
