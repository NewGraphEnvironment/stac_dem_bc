#!/bin/bash
# CI-safe S3 sync for the STAC catalog: item JSONs first, collection.json last.
#
# Built for a stateless runner whose $STAC_OUTPUT_DIR holds ONLY
# collection.json plus the newly created item JSONs. Two hard rules:
#
#   1. Never --delete. S3 is the only complete copy of the catalog
#      (58k+ items); syncing a partial local tree with --delete would
#      remove every object not present locally. (scripts/s3_sync.R is the
#      laptop full-catalog tool and keeps its --delete.)
#   2. Items before collection. collection.json links the new items, so it
#      uploads last — a failure mid-run then leaves unreferenced items
#      (harmless) rather than dangling links on the live collection.
#
# Credentials come from the default provider chain (OIDC in CI, local
# profile via AWS_PROFILE when run by hand).
#
# Usage: ./scripts/s3_sync-ci.sh [--dryrun]
# Env:   STAC_OUTPUT_DIR  local catalog dir (required)
#        STAC_S3_BUCKET   target (default: s3://stac-dem-bc)

set -euo pipefail

EXTRA_ARGS=()
if [ "${1:-}" = "--dryrun" ]; then
  EXTRA_ARGS=(--dryrun)
elif [ -n "${1:-}" ]; then
  echo "Usage: $0 [--dryrun]" >&2
  exit 1
fi

BUCKET="${STAC_S3_BUCKET:-s3://stac-dem-bc}"
# strip any trailing slash: "s3://bucket/" + "/collection.json" would write a
# hidden "/collection.json" key — cp succeeds, live collection never updates
BUCKET="${BUCKET%/}"

if [ -z "${STAC_OUTPUT_DIR:-}" ]; then
  echo "ERROR: STAC_OUTPUT_DIR is not set" >&2
  exit 1
fi
if [ ! -d "$STAC_OUTPUT_DIR" ]; then
  echo "ERROR: STAC_OUTPUT_DIR is not a directory: $STAC_OUTPUT_DIR" >&2
  exit 1
fi
if [ ! -s "$STAC_OUTPUT_DIR/collection.json" ]; then
  echo "ERROR: collection.json missing or empty in $STAC_OUTPUT_DIR" >&2
  exit 1
fi

ITEM_COUNT=$(find "$STAC_OUTPUT_DIR" -maxdepth 1 -type f -name "*.json" ! -name "collection.json" | wc -l | tr -d ' ')
echo "Uploading $ITEM_COUNT item JSON(s), then collection.json: $STAC_OUTPUT_DIR -> $BUCKET"

# ${EXTRA_ARGS[@]+...} keeps set -u happy on bash 3.2 (macOS) when the array is empty
aws s3 sync "$STAC_OUTPUT_DIR" "$BUCKET" \
  --exclude "collection.json" --exclude "*/.*" --exclude ".*" \
  ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}

aws s3 cp "$STAC_OUTPUT_DIR/collection.json" "$BUCKET/collection.json" \
  --content-type application/json \
  ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}

echo "Sync complete: $ITEM_COUNT item(s) + collection.json"
