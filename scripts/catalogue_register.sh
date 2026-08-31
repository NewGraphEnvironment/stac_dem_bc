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
#   STAC_BUCKET_URL  bucket serving collection.json (default: the stac-dem-bc
#                    bucket). MUST move together with STAC_COLLECTION -- they are
#                    two knobs over one fact, and the script reconciles them.
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

# Counting lines is a trap at both ends. `wc -l` misses a final line with no
# trailing newline, so a caller-supplied ids file is counted one short and the
# fetch guard then aborts a perfectly good run. `grep -c ''` counts it, but
# exits 1 on an EMPTY file -- which under `set -e` would kill the script on the
# zero-drift case, i.e. the routine one. Verified both directions.
count_lines() {
  local n
  n=$(grep -c '' "$1" 2>/dev/null) || n="${n:-0}"
  printf '%s' "${n:-0}"
}

echo "collection : $COLLECTION_ID"
echo "mode       : $MODE"

# --- the published set -------------------------------------------------------

echo "fetching published collection.json ..."
curl -fsSL --max-time 300 "$BUCKET_URL/collection.json" -o "$WORK/collection.json"
"$PY" scripts/register_manifest.py ids-published \
  --collection-file "$WORK/collection.json" > "$WORK/published.txt"
# STAC_COLLECTION and STAC_BUCKET_URL must move together. They are independent
# knobs over the same fact, and the API answers an unknown collection with
# 200 / zero features / no next link -- so pointing them at different collections
# reports every published item as missing, then "registers" them under an id the
# fetch never came from. Reconciled here rather than discovered later.
FILE_COLLECTION_ID=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("id",""))' \
  "$WORK/collection.json")
if [ "$FILE_COLLECTION_ID" != "$COLLECTION_ID" ]; then
  echo "ERROR: collection id mismatch." >&2
  echo "       STAC_COLLECTION  = $COLLECTION_ID" >&2
  echo "       collection.json  = $FILE_COLLECTION_ID  (from $BUCKET_URL)" >&2
  echo "       Set STAC_BUCKET_URL to the bucket for '$COLLECTION_ID'." >&2
  exit 1
fi

N_PUBLISHED=$(count_lines "$WORK/published.txt")
echo "published  : $N_PUBLISHED"

# Zero published items means the collection.json is truncated, or the wrong
# bucket was read -- never that the catalogue is legitimately empty. Without
# this, every mode reports success having done nothing, and --verify would
# announce IN SYNC against an empty set.
if [ "$N_PUBLISHED" -eq 0 ]; then
  echo "ERROR: no item links in the published collection.json — refusing to proceed" >&2
  exit 1
fi

# --- what to register --------------------------------------------------------

case "$MODE" in
  all)
    # Deduped for the same reason as --ids-file below: the count and the fetch
    # must be over the same set, or the guard fires on a healthy run.
    sort -u "$WORK/published.txt" > "$WORK/todo.txt"
    ;;
  ids-file)
    [ -s "$IDS_FILE" ] || { echo "ERROR: ids file missing or empty: $IDS_FILE" >&2; exit 1; }
    # Normalise: drop blank lines and guarantee a trailing newline. A
    # caller-supplied file with no final newline would otherwise be counted one
    # short by `wc -l` while Python reads every line — the fetch would then
    # produce one more file than expected and the count guard below would abort
    # a perfectly good run.
    # sort -u, not just a blank-line filter: `hrefs-published` matches against a
    # SET, so a duplicated id yields one fetch file while N_TODO counts two --
    # and the run then aborts after the whole fetch, reporting "fetched 2 of 3"
    # with zero failures and no explanation. Dedupe before anything counts.
    grep -v '^[[:space:]]*$' "$IDS_FILE" | sort -u > "$WORK/todo.txt" || true
    [ -s "$WORK/todo.txt" ] || { echo "ERROR: no ids in $IDS_FILE" >&2; exit 1; }
    ;;
  drift|verify)
    echo "enumerating registered ids (keyset paging; ~200s at full scale) ..."
    "$PY" scripts/register_manifest.py diff \
      --collection-file "$WORK/collection.json" \
      --collection-id "$COLLECTION_ID" \
      --api "$API" \
      --missing-out "$WORK/todo.txt" \
      --orphaned-out "$WORK/orphaned.txt"
    ;;
esac

N_TODO=$(count_lines "$WORK/todo.txt")
echo "to register: $N_TODO"

if [ "$MODE" = "verify" ]; then
  # Both directions, because the header and the docs promise both. `missing`
  # alone would have reported IN SYNC over any number of orphans -- registered
  # items with no published link -- which is the drift direction #28 is open
  # about and the one --all would silently preserve.
  # count_lines returns 0 for a MISSING file, which would make an unwritten
  # orphan list read as "no orphans" -- the gate silently disarmed. Require it.
  if [ ! -f "$WORK/orphaned.txt" ]; then
    echo "ERROR: orphan list was not written; cannot verify both directions" >&2
    exit 1
  fi
  N_ORPHANED=$(count_lines "$WORK/orphaned.txt")
  RC=0
  if [ "$N_TODO" -gt 0 ]; then
    echo "DRIFT: $N_TODO published item(s) are not registered" >&2
    head -5 "$WORK/todo.txt" | sed 's/^/  missing:  /' >&2
    RC=1
  fi
  if [ "$N_ORPHANED" -gt 0 ]; then
    echo "DRIFT: $N_ORPHANED registered item(s) are no longer published (#28)" >&2
    head -5 "$WORK/orphaned.txt" | sed 's/^/  orphaned: /' >&2
    RC=1
  fi
  # Absence of drift is an affirmative result and gets said out loud; a check
  # that prints nothing is indistinguishable from one that never ran.
  if [ "$RC" -eq 0 ]; then
    echo "IN SYNC: $N_PUBLISHED published, all registered, no orphans"
  fi
  exit "$RC"
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

# THE EXPECTATION IS DERIVED FROM THE ARTIFACT THE FETCHER CONSUMES.
#
# Both bugs found in review landed on the same pair: a count taken from one
# place (`todo.txt`) and files produced from another (`urls.txt`), with a guard
# comparing them. Deduping the inputs -- which is what the first two fixes did --
# leaves that pair free to disagree for the next reason. `urls.txt` is what the
# fetch loop actually iterates, so counting it is the only count that cannot
# drift from what the fetch produces.
cut -f2 "$WORK/hrefs.tsv" | sort -u > "$WORK/urls.txt"
N_URLS=$(count_lines "$WORK/urls.txt")

# One id must resolve to exactly one URL. It does today, but nothing in the
# published collection enforces it -- a duplicated item link would give one id
# two hrefs, which no amount of deduping `todo.txt` can reach because the
# duplication is on the href side. Reconciled explicitly rather than assumed,
# and reported as what it is rather than surfacing later as a phantom fetch
# shortfall.
if [ "$N_URLS" -ne "$N_TODO" ]; then
  echo "ERROR: $N_TODO id(s) resolved to $N_URLS distinct URL(s)." >&2
  echo "       The published collection.json has duplicate or missing item links." >&2
  exit 1
fi

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
    # Checked: an unchecked mv (ENOSPC mid-run) would exit 0 having produced no
    # file and logged no failure -- a shortfall with no diagnostic at all.
    if mv "$out.part" "$out"; then
      exit 0
    fi
    break
  fi
  sleep $((attempt * 2))
done
rm -f "$out.part"
printf '%s\n' "$url" >> "$dir/../failed.txt"
exit 1
FETCHEOF
chmod +x "$FETCH_SCRIPT"

echo "fetching $N_URLS item JSON(s) with $JOBS workers ..."
: > "$WORK/failed.txt"
# Failures are collected, not aborted on: xargs' exit status conflates "one
# transient 500" with "the bucket is gone", and the count guard below is the
# real gate.
set +e
# stderr goes to a file, never /dev/null: a curl that dies in a way the retry
# loop does not catch leaves its only diagnostic there.
xargs -P "$JOBS" -I {} "$FETCH_SCRIPT" {} "$FETCH_DIR" < "$WORK/urls.txt" 2>"$WORK/fetch_stderr.txt"
set -e

N_FETCHED=$(find "$FETCH_DIR" -maxdepth 1 -type f -name '*.json' | wc -l | tr -d ' ')
N_FAILED=$(count_lines "$WORK/failed.txt")
echo "fetched    : $N_FETCHED of $N_URLS ($N_FAILED failed after 3 attempts)"

if [ "$N_FETCHED" -ne "$N_URLS" ]; then
  echo "ERROR: fetched $N_FETCHED of $N_URLS — nothing sent to the database." >&2
  echo "       Failed URLs: $N_FAILED (re-run; upsert makes this safe to repeat)" >&2
  head -5 "$WORK/failed.txt" | sed 's/^/       /' >&2
  if [ -s "$WORK/fetch_stderr.txt" ]; then
    echo "       fetch stderr (last 5):" >&2
    tail -5 "$WORK/fetch_stderr.txt" | sed 's/^/       /' >&2
  fi
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
# Delegated to register_manifest.py rather than inlined, because the request
# body needs two details an inline heredoc would lose, each of which fails
# silently when omitted. The API's default limit is 10: a body without one
# returns the first 10 ids of however many were asked for, which reads as
# "590 of my 600 items are missing" and fails a verification whose subject was
# fine. And without `collections`, /search answers about every collection on
# the endpoint -- so during #34, when two collections share all 102,460 ids,
# verifying the new one would pass on the old one's rows. Both measured, and
# pinned by tests/test_register_manifest.py.
"$PY" scripts/register_manifest.py verify-serving \
  --ids-file "$WORK/todo.txt" --collection-id "$COLLECTION_ID" --api "$API"

echo "DONE: $N_TODO item(s) registered to $COLLECTION_ID"
