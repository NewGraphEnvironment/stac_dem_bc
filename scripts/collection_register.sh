#!/bin/bash
# Upsert a STAC collection into pgstac on the STAC host.
#
# The public API at images.a11s.one is read-only by design — it advertises no
# transaction conformance class, so POST/PUT return 405. Writes go through
# pypgstac on the host, which the server build installs at /opt/geoserv/scripts.
#
# REGISTER THE COLLECTION BEFORE ITS ITEMS. pgstac.items.collection is
#   NOT NULL REFERENCES collections(id) ON DELETE CASCADE
# so an item load with no collection row fails outright. Note this is the
# INVERSE of scripts/s3_sync-ci.sh, which uploads items first on purpose (a
# failure there leaves unreferenced items rather than dangling links). Two
# transports, opposite orders, both deliberate — do not "fix" one to match the
# other. That same CASCADE is the mechanism behind the 2026-08-29 outage: rtj's
# server-side script DELETEs the collection first, which takes every item with
# it, and it then failed before reloading.
#
# Upsert is idempotent — re-registering an existing collection updates in place
# and never leaves a window where the API serves nothing.
#
# Usage:
#   scripts/collection_register.sh [--dryrun] <collection.json>
#
# Env:
#   STAC_HOST  ssh target (default: root@geopro)
#   STAC_DB    pgstac database (default: stac)
#
# The host is root-only by design — cloud-init provisions no user account, so
# <user>@geopro gives "Permission denied (publickey)" and that is not a fault
# (rtj#193). Reached over the tailnet by MagicDNS rather than by address: the
# node joins untagged and so carries a 180-day key expiry, and it has already
# dropped off once and returned on a different tailnet IP (rtj#208). If the
# tailnet is the suspect, the DigitalOcean reserved IP 146.190.12.8 is the same
# machine — STAC_HOST=root@146.190.12.8. None of this is secret: that address
# is what images.a11s.one resolves to in public DNS. What is not public is
# access, which is the SSH key.

set -euo pipefail

HOST="${STAC_HOST:-root@geopro}"
DB="${STAC_DB:-stac}"

DRYRUN=0
if [ "${1:-}" = "--dryrun" ]; then
  DRYRUN=1
  shift
fi

if [ $# -ne 1 ]; then
  echo "Usage: $0 [--dryrun] <collection.json>" >&2
  exit 1
fi

COLLECTION="$1"

if [ ! -s "$COLLECTION" ]; then
  echo "ERROR: collection file missing or empty: $COLLECTION" >&2
  exit 1
fi

# Anything interpolated into the remote command string must not carry shell
# metacharacters. The reference implementation validates item ids this way and
# then interpolates its database name unchecked; both belong.
case "$DB" in
  *[!A-Za-z0-9_]*) echo "ERROR: suspicious database name: $DB" >&2; exit 1 ;;
esac

PY="${PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3

NDJSON=$(mktemp -t collection_register.XXXXXX)
trap 'rm -f "$NDJSON"' EXIT

# One collection, one line. Reuses the same compactor the item path uses.
printf '%s\n' "$COLLECTION" | "$PY" scripts/register_manifest.py ndjson --out "$NDJSON" >/dev/null

COLLECTION_ID=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$COLLECTION")
# Counted by parsing, not by grep -c: grep counts matching LINES, and a
# compact collection.json is ONE line -- so it reported "item links : 1" for a
# file carrying 102,460 of them. Informational only, never a guard, but a
# number that wrong in a release step is worse than no number.
ITEM_LINKS=$("$PY" -c 'import json,sys; print(sum(1 for l in json.load(open(sys.argv[1]))["links"] if l.get("rel")=="item"))' "$COLLECTION")
BYTES=$(wc -c < "$NDJSON" | tr -d ' ')

echo "collection : $COLLECTION_ID"
echo "item links : $ITEM_LINKS"
echo "payload    : $BYTES bytes"
echo "target     : $DB db on $HOST"

if [ "$DRYRUN" -eq 1 ]; then
  echo "[dryrun] nothing sent"
  exit 0
fi

# Probe before the expensive stage. A dead tailnet name otherwise surfaces as a
# resolution failure AFTER the payload is built — the same silent-after-success
# shape as the ARG_MAX bug this repo has been bitten by twice.
if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" true 2>/dev/null; then
  echo "ERROR: cannot reach $HOST. If the tailnet is down, try the reserved IP:" >&2
  echo "       STAC_HOST=root@146.190.12.8 $0 $*" >&2
  exit 1
fi

# The remote count guard is not redundant with a local one. If the local side
# dies mid-stream, the remote `cat` sees EOF and pypgstac loads a syntactically
# valid, truncated file — succeeding, and reporting success. Only the receiving
# side can tell a complete transfer from a short one.
ssh "$HOST" "
  set -euo pipefail
  t=\$(mktemp /tmp/stac_collection.XXXXXX.ndjson)
  trap 'rm -f \"\$t\"' EXIT
  cat > \"\$t\"
  n=\$(wc -l < \"\$t\" | tr -d ' ')
  if [ \"\$n\" -ne 1 ]; then
    echo \"FATAL: received \$n line(s), expected 1 — transfer truncated, nothing loaded\" >&2
    exit 1
  fi
  . /opt/geoserv/.env
  export PATH=/root/.local/bin:\$PATH
  export PGHOST=localhost PGPORT=5432 PGUSER=stac PGDATABASE=$DB
  export PGPASSWORD=\"\$POSTGRES_PASSWORD\"
  cd /opt/geoserv/scripts
  # No --dsn: pypgstac falls back to the PG* environment, which keeps the
  # password out of argv and so out of ps aux on a shared host.
  # --method upsert is explicit because the default is insert.
  uv run pypgstac load collections \"\$t\" --method upsert
" < "$NDJSON"

echo "OK — verify: curl -s https://images.a11s.one/collections/$COLLECTION_ID | head -c 200"
