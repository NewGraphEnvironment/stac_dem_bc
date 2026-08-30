#!/bin/bash
# Upsert STAC items into pgstac on the STAC host.
#
# Item JSON paths arrive on STDIN, one per line — never as arguments. This is
# not a style choice. The reference implementation in stac_uav_bc passes them as
# argv (`python3 - "$@"`), and at this catalogue's scale that is fatal: 102,460
# filenames is roughly 6 MB of argv against a ~2 MB ARG_MAX. Worse, it fails
# AFTER the expensive stage has already succeeded. Reading stdin makes the trap
# unreachable rather than merely avoided.
#
# Paths may contain spaces — 90 published items carry literal spaces and
# parentheses (#25) — so the list is newline-delimited and never word-split.
#
# REGISTER THE COLLECTION FIRST, via scripts/collection_register.sh.
# pgstac.items.collection is NOT NULL REFERENCES collections(id), so an item
# load with no collection row fails outright.
#
# Upsert is idempotent: re-registering an existing item updates it in place.
# There is deliberately no delete path in this repo — see
# scripts/collection_unregister.sh for the one guarded exception.
#
# Usage:
#   find "$DIR" -name '*.json' ! -name collection.json | scripts/item_register.sh
#   scripts/item_register.sh --dryrun < ids.txt
#
# Env:
#   STAC_HOST  ssh target (default: root@geopro)
#   STAC_DB    pgstac database (default: stac)
#
# See scripts/collection_register.sh for notes on the host, why it is root-only,
# and the reserved-IP fallback when the tailnet is the suspect.

set -euo pipefail

HOST="${STAC_HOST:-root@geopro}"
DB="${STAC_DB:-stac}"

DRYRUN=0
if [ "${1:-}" = "--dryrun" ]; then
  DRYRUN=1
  shift
elif [ -n "${1:-}" ]; then
  echo "Usage: $0 [--dryrun]   (item JSON paths on stdin, one per line)" >&2
  exit 1
fi

case "$DB" in
  *[!A-Za-z0-9_]*) echo "ERROR: suspicious database name: $DB" >&2; exit 1 ;;
esac

PY="${PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3

PATHS=$(mktemp -t item_register_paths.XXXXXX)
NDJSON=$(mktemp -t item_register.XXXXXX)
trap 'rm -f "$PATHS" "$NDJSON"' EXIT

# Drain stdin first: it is needed again below as the ssh payload channel.
cat > "$PATHS"
# `|| [ -n "$line" ]` so a final line with no trailing newline is not dropped.
EXPECTED=0
while IFS= read -r line || [ -n "$line" ]; do
  [ -n "$line" ] && EXPECTED=$((EXPECTED + 1))
done < "$PATHS"

# Zero is a real answer and gets its own branch. A deletions-only month, or a
# drift run against a catalogue already in sync, legitimately has nothing to do
# — that must exit 0 saying so, not sail through a vacuous `0 -eq 0` guard and
# hand pypgstac an empty file.
if [ "$EXPECTED" -eq 0 ]; then
  echo "nothing to register (0 items on stdin)"
  exit 0
fi

WRITTEN=$("$PY" scripts/register_manifest.py ndjson --out "$NDJSON" < "$PATHS")

if [ "$WRITTEN" -ne "$EXPECTED" ]; then
  echo "ERROR: assembled $WRITTEN line(s) from $EXPECTED path(s) — refusing to load" >&2
  exit 1
fi

BYTES=$(wc -c < "$NDJSON" | tr -d ' ')
echo "items   : $EXPECTED"
echo "payload : $BYTES bytes"
echo "target  : $DB db on $HOST"

if [ "$DRYRUN" -eq 1 ]; then
  echo "[dryrun] nothing sent"
  exit 0
fi

if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" true 2>/dev/null; then
  echo "ERROR: cannot reach $HOST. If the tailnet is down, try the reserved IP:" >&2
  echo "       STAC_HOST=root@146.190.12.8 $0" >&2
  exit 1
fi

# The count guard runs on the RECEIVING side. A local check cannot detect a
# truncated transfer: the remote `cat` sees EOF, pypgstac loads a syntactically
# valid short file, succeeds, and reports success. Only the receiver knows how
# many lines actually arrived.
#
# One `load` is one transaction, so a failure here rolls back whole. That is the
# central claim of this design and the reason it never chunks — chunking would
# make atomicity per-chunk and leave a mixed catalogue on failure.
ssh "$HOST" "
  set -euo pipefail
  t=\$(mktemp /tmp/stac_items.XXXXXX.ndjson)
  trap 'rm -f \"\$t\"' EXIT
  cat > \"\$t\"
  n=\$(wc -l < \"\$t\" | tr -d ' ')
  if [ \"\$n\" -ne $EXPECTED ]; then
    echo \"FATAL: received \$n line(s), expected $EXPECTED — transfer truncated, nothing loaded\" >&2
    exit 1
  fi
  . /opt/geoserv/.env
  export PATH=/root/.local/bin:\$PATH
  export PGHOST=localhost PGPORT=5432 PGUSER=stac PGDATABASE=$DB
  export PGPASSWORD=\"\$POSTGRES_PASSWORD\"
  cd /opt/geoserv/scripts
  # No --dsn: the PG* environment keeps the password out of argv, and so out of
  # ps aux. --method upsert is explicit because the default is insert.
  uv run pypgstac load items \"\$t\" --method upsert
  echo \"loaded \$n item(s)\"
" < "$NDJSON"

echo "OK — verify with: scripts/catalogue_register.sh --verify"
