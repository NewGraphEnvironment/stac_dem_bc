# Progress — update.yml: OIDC credentials expire an hour into the job (#60)

## Session 2026-10-13

- Plan-mode exploration — phases approved by user
- Created branch `60-update-yml-oidc-credentials-expire-an-ho` off main
- Scaffolded PWF baseline from issue #60 with approved phases
- Phase 1: `tests/test_workflow_credentials.py`, red against main (d349d7b)
- Phase 2: re-assume step before `Sync catalog to S3`, early step kept and commented as a probe; three mutations red, suite 313 passed (8f6b8a2)
- Phase 3: run 38452466305 confirmed as the `footprint: true` recovery (rewrite and sync succeeded; 454376b records drift + verify IN SYNC on 102,460); #60 body edited; filed #63 (a failed run commits `urls_list.txt`, stranding its new URLs)
- `/code-check branch`, four rounds (9436795, c44cd53, 6f3b048): rounds 2 and 3 each found a defect inside the previous fix; ended by round 3's independent enumeration of every signed AWS call in the repo, and a clean round 4 on the runtime guard
- Next: archive, PR
