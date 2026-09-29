## Outcome

`catalogue_register.sh --verify` and `--drift` compare published and registered item
**bodies**, not only ids. Every published body is fetched, both sides are digested
(sha256 of the canonical JSON minus `links`), and `--verify` reports `changed` beside
`missing` and `orphaned`. `--drift` registers missing ∪ changed. An unread body is an
error, never "unchanged". The collection's own body is compared too.

After any write, the served bodies are checked against the ones sent. After `--all`,
or a `--drift` whose collection changed, the whole catalogue is re-compared, because
pgstac serves items hydrated against their collection. A Python thread-pool fetcher
replaced one `curl` per item in every mode. Duplicate item links, and bodies that name
a different id, are refused before any write.

The main lesson: the 2,000-item sample said bodies round-trip exactly once `links` is
dropped, and the full run said otherwise twice. The research file carries the
durable version: [research/pgstac_round_trip.md](../../../research/pgstac_round_trip.md).
Follow-up: [stac_airphoto_bc#39](https://github.com/NewGraphEnvironment/stac_airphoto_bc/issues/39)
(switch its registration from `--all` to `--drift`).

## Measurement

Live, read-only, 2026-09-29. Full table in `findings.md`.

- **First full `--verify`: 160 `changed`, and nobody had touched them.** All were
  `"proj:epsg": null`, which pgstac strips, so the digest now drops null members.
- **Second run: 29 more.** PostGIS serves `-126.0` as `-126`, so integral floats are
  written as integers.
- **Final run: IN SYNC, 102,460 items, 0 changed; `--drift --dryrun` would upsert 0.**
  stac-airphoto-bc is IN SYNC across 10,100 items (2m26s).
- **Positive control:** with one body edited locally, `--verify` flagged exactly that
  id against real pgstac output.
- **Cost:** `--verify` went from 3m40s (ids only) to 12–26 min. API paging is a steady
  ~5.5 min; the S3 fetch varied from about 6 to 20 min across four runs.
- **Review:** the plan review had 27 findings. Code-check ran three rounds: round 1
  was Clean, round 2 found 2 latent defects and round 3 found 1. All three were the
  same mechanism: a guard that reads its artifact only after the loader has. The loop
  ended by enumeration: every `published_digests` raise now has a check before the
  write.

## Evidence

`logs/20260929_*_verify_content_45*.log` (gitignored, so local to the machine that
ran them; the numbers are copied into `findings.md`).

Closed by: PR (see branch `45-catalogue-register-sh-verify-drift-canno`)
