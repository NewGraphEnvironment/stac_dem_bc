#!/bin/bash
# Register the published catalogue into pgstac. One command, from any machine
# with tailnet SSH to the STAC host.
#
# This is where the monthly "someone registers it by hand" step goes to die. It
# was skipped for a month once and the API served 60,126 items against 98,040
# published (#31). --drift makes that condition self-correcting: it asks the API
# what it actually has, diffs against what S3 publishes, and registers the
# difference. Stateless — it needs no record of what previous runs did.
#
# Modes:
#   --drift       register only what the API is missing (the routine case)
#   --all         register every published item (~102k; recovery, or after #34)
#   --ids-file F  register exactly these ids
#   --verify      report drift in both directions and exit; register nothing
#
# Usage:
#   scripts/catalogue_register.sh --verify
#   scripts/catalogue_register.sh --drift
#   scripts/catalogue_register.sh --all --dryrun
#
# Env:
#   STAC_HOST        ssh target (default: root@geopro)
#   STAC_DB          pgstac database (default: stac)
#   STAC_COLLECTION  collection id (default: stac-dem-bc)
#   STAC_API         API base (default: https://images.a11s.one)
#   FETCH_JOBS       parallel S3 fetches (default: 20)
#
# Verification is by SET EQUALITY, in both directions, and never by a count.
# The API has no aggregation extension (/aggregate 404s) and returns
# numberMatched: null, and a /search on a list of ids silently omits the ones
# that do not exist — so "I asked for N and got N back" can be true while the
# sets differ. Measured: 2 ids requested with 1 bogus returns 1 feature, no error.

set -euo pipefail

HOST="${STAC_HOST:-root@geopro}"
DB="${STAC_DB:-stac}"
COLLECTION_ID="${STAC_COLLECTION:-stac-dem-bc}"
API="${STAC_API:-https://images.a11s.one}"
JOBS="${FETCH_JOBS:-20}"
BUCKET_URL="${STAC_BUCKET_URL:-https://stac-dem-bc.s3.amazonaws.com}"

MODE=""
IDS_FILE=""
DRYRUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --drift|--all|--verify) MODE="${1#--}" ;;
    --ids-file) MODE="ids-file"; IDS_FILE="${2:?--ids-file needs a path}"; shift ;;
    --dryrun) DRYRUN=1 ;;
    *) echo "Usage: $0 [--dryrun] (--drift | --all | --ids-file F | --verify)" >&2; exit 1 ;;
  esac
  shift
done

if [ -z "$MODE" ]; then
  echo "Usage: $0 [--dryrun] (--drift | --all | --ids-file F | --verify)" >&2
  exit 1
fi

PY="${PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3

WORK=$(mktemp -d -t catalogue_register.XXXXXX)
trap 'rm -rf "$WORK"' EXIT

echo "collection : $COLLECTION_ID"
echo "mode       : $MODE"

# --- the published set -------------------------------------------------------

echo "fetching published collection.json ..."
curl -fsSL --max-time 300 "$BUCKET_URL/collection.json" -o "$WORK/collection.json"
"$PY" scripts/register_manifest.py ids-published \
  --collection-file "$WORK/collection.json" > "$WORK/published.txt"
N_PUBLISHED=$(wc -l < "$WORK/published.txt" | tr -d ' ')
echo "published  : $N_PUBLISHED"

# --- what to register --------------------------------------------------------

case "$MODE" in
  all)
    cp "$WORK/published.txt" "$WORK/todo.txt"
    ;;
  ids-file)
    [ -s "$IDS_FILE" ] || { echo "ERROR: ids file missing or empty: $IDS_FILE" >&2; exit 1; }
    cp "$IDS_FILE" "$WORK/todo.txt"
    ;;
  drift|verify)
    echo "enumerating registered ids (keyset paging; ~200s at full scale) ..."
    "$PY" scripts/register_manifest.py diff \
      --collection-file "$WORK/collection.json" \
      --collection-id "$COLLECTION_ID" \
      --api "$API" \
      --missing-out "$WORK/todo.txt"
    ;;
esac

N_TODO=$(wc -l < "$WORK/todo.txt" | tr -d ' ')
echo "to register: $N_TODO"

if [ "$MODE" = "verify" ]; then
  # Absence of drift is an affirmative result and gets said out loud; an empty
  # loop that prints nothing is indistinguishable from a check that never ran.
  if [ "$N_TODO" -eq 0 ]; then
    echo "IN SYNC: every published item is registered"
    exit 0
  fi
  echo "DRIFT: $N_TODO published item(s) are not registered" >&2
  head -5 "$WORK/todo.txt" | sed 's/^/  missing: /' >&2
  exit 1
fi

if [ "$N_TODO" -eq 0 ]; then
  echo "nothing to register — already in sync"
  exit 0
fi

# --- resolve fetch hrefs -----------------------------------------------------

# Always fetch by the PUBLISHED href, never by a URL rebuilt from an id. 90 ids
# carry literal spaces and parentheses; the published href is already correctly
# percent-encoded and a reconstructed one is not (#25).
"$PY" scripts/register_manifest.py hrefs-published \
  --collection-file "$WORK/collection.json" \
  --ids-file "$WORK/todo.txt" > "$WORK/hrefs.tsv"
cut -f2 "$WORK/hrefs.tsv" > "$WORK/urls.txt"

if [ "$DRYRUN" -eq 1 ]; then
  echo "[dryrun] would fetch $N_TODO item(s) and upsert them to $DB on $HOST"
  echo "[dryrun] first 3:"
  head -3 "$WORK/hrefs.tsv" | sed 's/^/  /'
  exit 0
fi

# Probe before the expensive stage — a dead host otherwise surfaces only after
# a multi-minute fetch, which is the same silent-after-success shape as ARG_MAX.
if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" true 2>/dev/null; then
  echo "ERROR: cannot reach $HOST. If the tailnet is down, try the reserved IP:" >&2
  echo "       STAC_HOST=root@146.190.12.8 $0 --$MODE" >&2
  exit 1
fi

# --- fetch -------------------------------------------------------------------

FETCH_DIR="$WORK/items"
mkdir -p "$FETCH_DIR"
FETCH_SCRIPT="$WORK/fetch_one.sh"

# Each worker writes its OWN file. Parallel appends to one shared descriptor
# interleave mid-record once a record exceeds a single write(), which corrupts
# the NDJSON. The filename is a hash of the URL so ids containing spaces and
# parentheses need no quoting anywhere downstream.
cat > "$FETCH_SCRIPT" <<'FETCHEOF'
#!/bin/bash
url="$1"; dir="$2"
if command -v md5sum >/dev/null 2>&1; then
  key=$(printf '%s' "$url" | md5sum | cut -d' ' -f1)
else
  key=$(printf '%s' "$url" | md5 -q)
fi
out="$dir/$key.json"
# --max-time so one hung connection cannot pin a worker slot indefinitely: a
# wedged pool and a slow pool look identical from outside.
# Retry in-process, so a transient failure never reaches the exit code. A
# backfill in this repo once completed 98,040 items and threw the run away over
# 2 transient errors (0.002%).
for attempt in 1 2 3; do
  if curl -sfL --max-time 60 "$url" -o "$out.part" && [ -s "$out.part" ]; then
    # Write then rename: `curl > file` truncates before curl runs, so a failed
    # fetch would otherwise leave a zero-byte file that counts as present.
    mv "$out.part" "$out"
    exit 0
  fi
  sleep $((attempt * 2))
done
rm -f "$out.part"
printf '%s\n' "$url" >> "$dir/../failed.txt"
exit 1
FETCHEOF
chmod +x "$FETCH_SCRIPT"

echo "fetching $N_TODO item JSON(s) with $JOBS workers ..."
: > "$WORK/failed.txt"
# Failures are collected, not aborted on: xargs' exit status conflates "one
# transient 500" with "the bucket is gone", and the count guard below is the
# real gate.
set +e
xargs -P "$JOBS" -I {} "$FETCH_SCRIPT" {} "$FETCH_DIR" < "$WORK/urls.txt" 2>/dev/null
set -e

N_FETCHED=$(find "$FETCH_DIR" -maxdepth 1 -type f -name '*.json' | wc -l | tr -d ' ')
N_FAILED=$(wc -l < "$WORK/failed.txt" | tr -d ' ')
echo "fetched    : $N_FETCHED of $N_TODO ($N_FAILED failed after 3 attempts)"

if [ "$N_FETCHED" -ne "$N_TODO" ]; then
  echo "ERROR: fetched $N_FETCHED of $N_TODO — nothing sent to the database." >&2
  echo "       Failed URLs: $N_FAILED (re-run; upsert makes this safe to repeat)" >&2
  head -5 "$WORK/failed.txt" | sed 's/^/       /' >&2
  exit 1
fi

# --- register: collection first, then items ---------------------------------

# The FK ordering. pgstac.items.collection REFERENCES collections(id), so items
# with no collection row fail outright.
./scripts/collection_register.sh "$WORK/collection.json"

# find, never a glob: 102k filenames is ~6 MB of argv against a ~2 MB ARG_MAX,
# and it would fail after the fetch had already succeeded.
find "$FETCH_DIR" -maxdepth 1 -type f -name '*.json' | ./scripts/item_register.sh

# --- verify ------------------------------------------------------------------

echo "verifying by set equality ..."
"$PY" - "$COLLECTION_ID" "$API" "$WORK/todo.txt" <<'PYEOF'
import json, sys, requests

collection_id, api, todo_path = sys.argv[1], sys.argv[2], sys.argv[3]
wanted = [l.rstrip("\n") for l in open(todo_path) if l.strip()]

# Batched, because a URL-length limit is a real ceiling on a single query.
got = set()
for i in range(0, len(wanted), 500):
    chunk = wanted[i:i + 500]
    r = requests.post(f"{api.rstrip('/')}/search",
                      json={"ids": chunk, "fields": {"include": ["id"]}},
                      timeout=120)
    r.raise_for_status()
    got.update(f["id"] for f in r.json().get("features", []))

missing = sorted(set(wanted) - got)
print(f"requested {len(wanted)}, serving {len(got)}")
if missing:
    print(f"FAIL: {len(missing)} id(s) registered but not served, e.g. {missing[:3]}",
          file=sys.stderr)
    sys.exit(1)
print("OK: every registered id is served by the API")
PYEOF

echo "DONE: $N_TODO item(s) registered to $COLLECTION_ID"
