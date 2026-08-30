#!/bin/bash
# Delete a collection and its items from pgstac. THE ONLY DESTRUCTIVE SCRIPT
# IN THIS REPO.
#
# It exists for #34's cutover: once stac-elevation-bc is loaded and verified,
# the old stac-dem-bc rows come out. Nothing in the routine path calls this —
# registration is upsert-only precisely so that no ordinary operation can empty
# the API.
#
# Deletion has no pypgstac verb, so it goes through pgstac SQL. rtj's
# stac_unregister.sh is NOT the tool: it drives the API's transaction routes,
# which are switched off, so it returns 405 and always has.
#
# WHAT THIS COSTS IF YOU GET IT WRONG. pgstac.items.collection is
# ON DELETE CASCADE, so removing the collection row removes every item with it.
# That is the exact mechanism that took images.a11s.one to zero items on
# 2026-08-29. There is no undo; the recovery path is a full re-register from S3,
# which is scripts/catalogue_register.sh --all and takes the better part of an
# hour.
#
# Usage:
#   scripts/collection_unregister.sh <collection-id>          # reports, deletes nothing
#   scripts/collection_unregister.sh --yes <collection-id>    # deletes
#
# Env:
#   STAC_HOST  ssh target (default: root@geopro)
#   STAC_DB    pgstac database (default: stac)

set -euo pipefail

HOST="${STAC_HOST:-root@geopro}"
DB="${STAC_DB:-stac}"

CONFIRM=0
if [ "${1:-}" = "--yes" ]; then
  CONFIRM=1
  shift
fi

if [ $# -ne 1 ]; then
  echo "Usage: $0 [--yes] <collection-id>" >&2
  exit 1
fi

COLLECTION_ID="$1"

# The id is interpolated into SQL, so it is validated against an allowlist
# rather than escaped. Collection ids in this catalogue family are
# [a-z0-9-] throughout; anything else is a mistake or an injection.
case "$COLLECTION_ID" in
  *[!A-Za-z0-9_-]*|"")
    echo "ERROR: refusing suspicious collection id: '$COLLECTION_ID'" >&2
    exit 1
    ;;
esac
case "$DB" in
  *[!A-Za-z0-9_]*) echo "ERROR: suspicious database name: $DB" >&2; exit 1 ;;
esac

if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" true 2>/dev/null; then
  echo "ERROR: cannot reach $HOST" >&2
  exit 1
fi

# Count BEFORE asking, so the number in front of the operator is the real one
# rather than one they remember from earlier.
COUNT=$(ssh "$HOST" "docker exec geoserv-db psql -U stac -d $DB -tAc \
  \"select count(*) from pgstac.items where collection = '$COLLECTION_ID';\"" | tr -d ' ')

if ! [ "$COUNT" -eq "$COUNT" ] 2>/dev/null; then
  echo "ERROR: could not read the item count (got: '$COUNT') — refusing to delete" >&2
  exit 1
fi

echo "collection : $COLLECTION_ID"
echo "database   : $DB on $HOST"
if [ "$COUNT" -gt 0 ]; then
  echo "items      : $COUNT  <-- these will be destroyed"
else
  echo "items      : 0"
fi

# A collection that is already absent is not an error, but it is also not a
# deletion, and the two must not print the same thing.
if [ "$COUNT" -eq 0 ]; then
  EXISTS=$(ssh "$HOST" "docker exec geoserv-db psql -U stac -d $DB -tAc \
    \"select count(*) from pgstac.collections where id = '$COLLECTION_ID';\"" | tr -d ' ')
  # Same numeric guard as $COUNT above. Without it an unreadable value makes
  # the test fail toward the DESTRUCTIVE branch, which is the wrong direction
  # for the only script here that destroys anything.
  if ! [ "$EXISTS" -eq "$EXISTS" ] 2>/dev/null; then
    echo "ERROR: could not read the collection row (got: '$EXISTS')" >&2
    exit 1
  fi
  if [ "$EXISTS" -eq 0 ]; then
    echo "nothing to do: no such collection registered"
    exit 0
  fi
  echo "note: collection row exists with no items"
fi

if [ "$CONFIRM" -ne 1 ]; then
  echo
  echo "Nothing deleted. Re-run with --yes to destroy the above:"
  echo "  $0 --yes $COLLECTION_ID"
  exit 0
fi

echo "deleting ..."
ssh "$HOST" "docker exec -i geoserv-db psql -U stac -d $DB -v ON_ERROR_STOP=1" <<SQL
BEGIN;
DELETE FROM pgstac.items WHERE collection = '$COLLECTION_ID';
DELETE FROM pgstac.collections WHERE id = '$COLLECTION_ID';
COMMIT;
SQL

echo "OK — deleted $COLLECTION_ID ($COUNT items)"
echo "Recovery if this was a mistake: scripts/catalogue_register.sh --all"
