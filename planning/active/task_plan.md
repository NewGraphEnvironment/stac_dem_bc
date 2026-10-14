# Task: update.yml: OIDC credentials expire an hour into the job, so a long run's S3 sync fails (#60)

`update.yml` assumes the OIDC role once, right after checkout (`aws-actions/configure-aws-credentials@v4`, no `role-duration-seconds`), so the session lasts one hour from that step. Any S3 write more than an hour into the job fails with `ExpiredToken`. Measured on the `footprint` dispatch, run 38339265803 (2026-10-13): credentials assumed 03:01:28, sync started 03:50:05, first `ExpiredToken` 04:01:29; 91,772 items reached S3 and `collection.json` was not written.

Exploration: the sync (`scripts/s3_sync-ci.sh`) is the only step that uses the credential; every other S3 read is unauthenticated HTTPS. Keep the early step as a fail-fast probe of the trust policy (#40 moves it), and re-assume immediately before the sync.

## Phase 1: Test first
- [x] Add `tests/test_workflow_credentials.py`: every step calling `aws ` or `s3_sync-ci.sh` is immediately preceded by a `configure-aws-credentials` step with the same `if:`; every credentials step names the same role and region
- [x] Run it and confirm it fails against the current workflow

## Phase 2: The fix
- [x] Add `configure-aws-credentials@v4` immediately before `Sync catalog to S3`, same role and region, carrying the sync's `if:`
- [x] Comment both credential steps (probe vs. re-assume, citing run 38339265803)
- [x] Update the header comment block of `update.yml` if it describes auth (no change: it names the role and OIDC, nothing about a single assume)
- [x] Tests green; restore the bug and confirm the new test goes red

## Phase 3: Issues and records
- [x] Confirm run 38452466305 was the `footprint: true` recovery and its sync succeeded; edit #60's body to record it
- [x] File the `urls_list.txt` stranding issue, linking #57 and #60 (#63)
- [x] Session note in `progress.md`

## Validation

- [ ] Tests pass
- [ ] `/code-check` clean (each commit, or once over the branch with `/code-check branch`)
- [ ] PWF checkboxes match landed work
- [ ] `/planning-archive` on completion
