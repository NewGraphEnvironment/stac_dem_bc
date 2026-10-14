## Outcome

`update.yml` assumed its OIDC role once, at the top of the job, and the one-hour session expired partway through a long run's S3 sync. The sync is the only step that uses AWS credentials (every other S3 read is anonymous HTTPS), so it now re-assumes the role in a step immediately before it, with the same `if:`. The early step stays as a fail-fast probe of the trust policy (#40 will move that condition), and code-check round 3 changed it to `output-env-credentials: false`: it still assumes the role but exports nothing. So any future AWS step that skips its own re-assume fails on every run, not only on runs longer than an hour. `tests/test_workflow_credentials.py` pins the layout. It took four review rounds because the first two each found a spelling of AWS use that the test's detector missed, and the third showed the detector's ground truth had been enumerated in the detector's own vocabulary. The way out was to test the property at run time, not to widen the regex again. Found on the way and filed separately: a failed run commits `data/urls_list.txt`, which strands its new URLs (#63).

## Measurement

Run 38339265803 (footprint dispatch, 2026-10-13): credentials assumed 03:01:28 UTC, sync started 03:50:05, first `ExpiredToken` 04:01:29. 91,772 of 102,460 items had uploaded in that ~11 min, so a full sync takes about 13 min, comfortably inside one fresh session. That number is what made "re-assume before the sync" sufficient without raising `role-duration-seconds` (which would also need the role's maximum raised in the infrastructure repo). Recovery: run 38452466305 re-dispatched `footprint: true` (19:08–19:44 UTC), and its rewrite and sync both succeeded; 454376b records drift registration and `stacs verify` IN SYNC on 102,460.

The new step's first real use is the next run that publishes: a dispatch or the 3 Nov cron. `workflow_dispatch` runs only from `main`, so it could not be exercised before merge.

## Evidence

GitHub Actions runs 38339265803 (failure) and 38452466305 (recovery). Review rounds: `review-round*.md` in this directory.

Closed by: PR (see branch `60-update-yml-oidc-credentials-expire-an-ho`)
