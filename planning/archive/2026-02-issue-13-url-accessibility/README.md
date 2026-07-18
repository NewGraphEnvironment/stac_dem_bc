# Issue #13 — Source URL Accessibility Validation

**Closed:** 2026-02-17 · **Branch:** `13-fix-s3-permissions`

## Outcome

Added source-URL accessibility checking to the pipeline: `check_url_accessible()` in `scripts/stac_utils.py` and `scripts/urls_check_access.py` (parallel HTTP HEAD checks with an incremental CSV cache at `data/urls_access_checks.csv`, shareable with GeoBC). Integrated into `scripts/build_safe.sh` as Step 3.5, warn-only — inaccessible sources are logged but don't block the build, since GeoTIFF validation skips unreadable files downstream.

The original trigger (6 items under `092p045` returning 403 from the BC objectstore) resolved upstream during the work — GeoBC fixed the permissions and all known-bad URLs returned 200 by close. The script remains as ongoing monitoring for new URLs.

Archived 2026-07-18 while initializing issue #23 (monthly automation), which wires `urls_check_access.py` into the GitHub Actions workflow as a warn-only step — the same semantics established here.
