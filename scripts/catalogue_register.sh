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
#   --drift       register what the API is missing or serves with a different
#                 body (the routine case)
#   --all         register every published item (~102k; recovery, or after #34)
#   --ids-file F  register exactly these ids
#   --verify      report missing, changed and orphaned items and the collection's
#                 state, and exit; register nothing
#
# --drift and --verify fetch EVERY published body, because whether an item needs
# registering is a question about its content, not only its id (#45): a rebuild
# keeps every id. So --drift --dryrun fetches the whole catalogue too.
#
# Usage:
#   scripts/catalogue_register.sh --verify
#   scripts/catalogue_register.sh --drift
#   scripts/catalogue_register.sh --all --dryrun
#
# Env:
#   STAC_HOST        ssh target (default: root@geopro)
#   STAC_DB          pgstac database (default: stac)
#   STAC_COLLECTION  collection id (default: collection_patch.COLLECTION_ID)
#   STAC_BUCKET_URL  bucket serving collection.json (default:
#                    stac_utils.PATH_S3_STAC, the stac-dem-bc bucket). These
#                    were once two knobs over ONE fact, because the collection
#                    and the bucket shared a name. Since #34 they are genuinely two -- the collection is stac-elevation-bc and
#                    the bucket is still stac-dem-bc -- so neither can be derived
#                    from the other, and the script reconciles them at runtime
#                    against the id inside the fetched collection.json instead.
#   STAC_REQUIRE_ASSET  ONE asset key every item must carry. Other collections
#                    only -- refused when the collection or the bucket is this
#                    repo's, whose rules come from stac_utils / item_migrate
#                    (#42). Unset: no requirement.
#   STAC_FORBID_ASSET   asset key(s), comma-separated, no item may carry. Same
#                    scope as STAC_REQUIRE_ASSET.
#   STAC_API         API base (default: https://images.a11s.one)
#   FETCH_JOBS       parallel S3 fetches (default: 20)
#
# Verification is by SET EQUALITY, in both directions, and never by a count.
# The API has no aggregation extension (/aggregate 404s) and returns
# numberMatched: null, and a /search on a list of ids silently omits the ones
# that do not exist — so "I asked for N and got N back" can be true while the
# sets differ. Measured: 2 ids requested with 1 bogus returns 1 feature, no error.
#
# And by CONTENT (#45): equal id sets say nothing about bodies. Each side is
# reduced to a digest by register_manifest.body_digest -- links removed, and the
# two things pgstac's round trip does not preserve (null members, integral
# floats) canonicalised -- and an unread body is an error, never "unchanged".

set -euo pipefail

PY="${PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3

HOST="${STAC_HOST:-root@geopro}"
DB="${STAC_DB:-stac}"
# Read from collection_patch rather than repeated here. A second literal is a
# second definition, and the two would disagree exactly once -- during a rename,
# which is when being wrong is most expensive.
#
# Assigned and THEN tested, not tested inline: a failing command substitution
# leaves an empty string, and an empty collection id reads as "no collection"
# rather than as "the lookup broke". Failing loudly beats falling back to a
# literal that is stale by construction.
#
# Read even when STAC_COLLECTION is set: the audit below needs to know whether
# the collection being registered is this repo's (#42).
OWN_COLLECTION_ID=$("$PY" -c 'import sys; sys.path.insert(0, "scripts"); import collection_patch; print(collection_patch.COLLECTION_ID)' 2>/dev/null) || OWN_COLLECTION_ID=""
if [ -z "$OWN_COLLECTION_ID" ]; then
  echo "ERROR: could not read COLLECTION_ID from scripts/collection_patch.py." >&2
  echo "       Run from the repo root." >&2
  exit 1
fi
COLLECTION_ID="${STAC_COLLECTION:-$OWN_COLLECTION_ID}"
API="${STAC_API:-https://images.a11s.one}"
JOBS="${FETCH_JOBS:-20}"
# From stac_utils for the same reason as the collection id: the audit policy
# below compares against it, and a second literal would be a second definition.
OWN_BUCKET_URL=$("$PY" -c 'import sys; sys.path.insert(0, "scripts"); import stac_utils; print(stac_utils.PATH_S3_STAC)') || OWN_BUCKET_URL=""
if [ -z "$OWN_BUCKET_URL" ]; then
  echo "ERROR: could not read PATH_S3_STAC from scripts/stac_utils.py." >&2
  exit 1
fi
BUCKET_URL="${STAC_BUCKET_URL:-$OWN_BUCKET_URL}"

# --- what the pre-load audit asserts about assets (#42) ----------------------
#
# The audit always checks that every item names COLLECTION_ID and that the count
# matches. The asset half depends on whose catalogue this is:
#
#   this repo's             require stac_utils.ASSET_DEM, forbid
#                           item_migrate.ASSET_RENAMES -- the #34 guard, read
#                           from the modules and never spelled here
#   anyone else's           STAC_REQUIRE_ASSET / STAC_FORBID_ASSET if set,
#                           otherwise no asset check (and it says so)
#
# "This repo's" is decided from three facts, any one of which is enough:
#
#   1. the collection id is collection_patch.COLLECTION_ID
#   2. STAC_BUCKET_URL names stac_utils.PATH_S3_STAC's bucket -- by bucket NAME,
#      not URL string: regional and global endpoints, http and https,
#      path-style and any-case hosts all serve the same collection.json
#   3. any item link in the fetched collection.json points into that bucket
#
# The id alone has a hole in exactly the case #34 was about: between merging a
# rename and running the cutover, the bucket still publishes the OLD id, and an
# operator who sets STAC_COLLECTION to it (which the mismatch error below says
# not to do) passes the id reconciliation -- and would load old-shape items with
# no asset check. (2) catches that before anything is fetched. (3) is the one
# that cannot be spelled around: a URL can reach the bucket through a spelling
# no parser knows (a file:// copy of collection.json, a CNAME, a CDN), but the
# item hrefs are what the fetch actually reads.
#
# The env knobs are refused for this repo's catalogue rather than honoured as
# overrides. They exist to give another collection a check, not to loosen the
# one that caught a half-done rename here.
use_own_asset_rules() {
  local var
  for var in STAC_REQUIRE_ASSET STAC_FORBID_ASSET; do
    if [ -n "${!var:-}" ]; then
      echo "ERROR: $var is set, but this is this repo's catalogue ($1)." >&2
      echo "       Its asset rules come from stac_utils / item_migrate and cannot" >&2
      echo "       be overridden. $var applies to other collections only." >&2
      exit 1
    fi
  done
  AUDIT_REQUIRE=$("$PY" -c 'import sys; sys.path.insert(0, "scripts"); import stac_utils; print(stac_utils.ASSET_DEM)') || AUDIT_REQUIRE=""
  AUDIT_FORBID=$("$PY" -c 'import sys; sys.path.insert(0, "scripts"); import item_migrate; print(",".join(item_migrate.ASSET_RENAMES))') || AUDIT_FORBID=""
  # Both, not just one: an empty forbid list would quietly stop checking for
  # the retired key while every run still printed OK.
  if [ -z "$AUDIT_REQUIRE" ] || [ -z "$AUDIT_FORBID" ]; then
    echo "ERROR: could not read the asset key constants" >&2
    exit 1
  fi
  AUDIT_OWN=1
}

describe_asset_rules() {
  AUDIT_ASSET_ARGS=()
  if [ -n "$AUDIT_REQUIRE" ]; then AUDIT_ASSET_ARGS+=(--require-asset "$AUDIT_REQUIRE"); fi
  if [ -n "$AUDIT_FORBID" ]; then AUDIT_ASSET_ARGS+=(--forbid-asset "$AUDIT_FORBID"); fi
  if [ -n "$AUDIT_REQUIRE$AUDIT_FORBID" ]; then
    AUDIT_DESC="require=${AUDIT_REQUIRE:--} forbid=${AUDIT_FORBID:--}"
  else
    AUDIT_DESC="none (not this repo's catalogue; set STAC_REQUIRE_ASSET / STAC_FORBID_ASSET to enable)"
  fi
}

SAME_BUCKET=$("$PY" scripts/register_manifest.py same-bucket "$BUCKET_URL" "$OWN_BUCKET_URL") || SAME_BUCKET=""
case "$SAME_BUCKET" in
  same|different) ;;
  *) echo "ERROR: could not compare $BUCKET_URL with this repo's bucket" >&2; exit 1 ;;
esac
AUDIT_OWN=0
if [ "$COLLECTION_ID" = "$OWN_COLLECTION_ID" ]; then
  use_own_asset_rules "collection '$COLLECTION_ID'"
elif [ "$SAME_BUCKET" = "same" ]; then
  use_own_asset_rules "bucket $BUCKET_URL"
else
  # One key for REQUIRE (audit-items takes a single --require-asset), a
  # comma-separated list for FORBID. What audit-items actually applies is
  # printed by audit-items itself, so a value that parses to nothing (",")
  # shows up as "no asset checks" there rather than as a check here.
  AUDIT_REQUIRE="${STAC_REQUIRE_ASSET:-}"
  AUDIT_FORBID="${STAC_FORBID_ASSET:-}"
fi
describe_asset_rules

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
echo "asset audit: $AUDIT_DESC"

# --- the published set -------------------------------------------------------

echo "fetching published collection.json ..."
curl -fsSL --max-time 300 "$BUCKET_URL/collection.json" -o "$WORK/collection.json"
"$PY" scripts/register_manifest.py ids-published \
  --collection-file "$WORK/collection.json" > "$WORK/published.txt"
# STAC_COLLECTION and STAC_BUCKET_URL name two DIFFERENT things since #34 -- the
# collection is stac-elevation-bc, the bucket is still stac-dem-bc -- so neither
# can be checked against the other by name. What reconciles them is the id
# INSIDE the fetched file. It matters because the API answers an unknown
# collection with 200 / zero features / no next link, so a mismatched pair
# reports every published item as missing and then "registers" them under an id
# the fetch never came from. Caught here rather than discovered later.
FILE_COLLECTION_ID=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("id",""))' \
  "$WORK/collection.json")
if [ "$FILE_COLLECTION_ID" != "$COLLECTION_ID" ]; then
  echo "ERROR: collection id mismatch." >&2
  echo "       expecting        = $COLLECTION_ID  (scripts/collection_patch.py)" >&2
  echo "       collection.json  = $FILE_COLLECTION_ID  (from $BUCKET_URL)" >&2
  echo >&2
  echo "       Two causes, and they want opposite fixes:" >&2
  echo >&2
  echo "       1. The published catalogue has not been migrated to" >&2
  echo "          '$COLLECTION_ID' yet. This is the EXPECTED state between" >&2
  echo "          merging a rename and running the cutover. Run the migration" >&2
  echo "          (workflow_dispatch, rename=true) -- do not set" >&2
  echo "          STAC_COLLECTION to make this pass, which would register the" >&2
  echo "          old catalogue under a name the code no longer uses." >&2
  echo >&2
  echo "       2. STAC_BUCKET_URL points at a different catalogue's bucket." >&2
  echo "          Set it to the bucket serving '$COLLECTION_ID'. Note the" >&2
  echo "          bucket and the collection do NOT share a name here." >&2
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

# Fact (3) of "this repo's catalogue" -- see the asset-rules block at the top.
# Before any mode can write, and before --dryrun exits, so a dryrun shows the
# rules a real run would apply.
if [ "$AUDIT_OWN" -eq 0 ]; then
  N_OWN_LINKS=$("$PY" scripts/register_manifest.py hrefs-in-bucket \
    --collection-file "$WORK/collection.json" --bucket-url "$OWN_BUCKET_URL") || N_OWN_LINKS=""
  case "$N_OWN_LINKS" in
    ''|*[!0-9]*) echo "ERROR: could not check item links against this repo's bucket" >&2; exit 1 ;;
  esac
  if [ "$N_OWN_LINKS" -gt 0 ]; then
    use_own_asset_rules "$N_OWN_LINKS item link(s) point into $OWN_BUCKET_URL"
    describe_asset_rules
    echo "asset audit: $AUDIT_DESC  ($N_OWN_LINKS item link(s) are in this repo's bucket)"
  fi
fi

# --- what to fetch -----------------------------------------------------------
#
# --all and --ids-file know what they will register before fetching anything,
# so they fetch exactly that. --drift and --verify cannot: whether an item needs
# registering is a question about its BODY, not just its id (#45). A rebuild
# keeps every id, so an id comparison reported IN SYNC over a catalogue whose
# every body had changed. They fetch every published body and decide after.

case "$MODE" in
  all)
    # Deduped for the same reason as --ids-file below: the count and the fetch
    # must be over the same set, or the guard fires on a healthy run.
    sort -u "$WORK/published.txt" > "$WORK/todo.txt"
    cp "$WORK/todo.txt" "$WORK/fetch_ids.txt"
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
    cp "$WORK/todo.txt" "$WORK/fetch_ids.txt"
    ;;
  drift|verify)
    # LC_ALL=C: `sort -u` dedupes by COLLATION, and in a UTF-8 locale two
    # distinct ids can collate equal and be merged into one. Bytes cannot.
    LC_ALL=C sort -u "$WORK/published.txt" > "$WORK/fetch_ids.txt"
    ;;
esac
N_FETCH_IDS=$(count_lines "$WORK/fetch_ids.txt")

# Always fetch by the PUBLISHED href, never by a URL rebuilt from an id. 90 ids
# carry literal spaces and parentheses; the published href is already correctly
# percent-encoded and a reconstructed one is not (#25).
"$PY" scripts/register_manifest.py hrefs-published \
  --collection-file "$WORK/collection.json" \
  --ids-file "$WORK/fetch_ids.txt" > "$WORK/hrefs.tsv"

# THE EXPECTATION IS DERIVED FROM THE ARTIFACT THE FETCHER CONSUMES.
#
# Both bugs found in review landed on the same pair: a count taken from one
# place (`todo.txt`) and files produced from another (`urls.txt`), with a guard
# comparing them. Deduping the inputs -- which is what the first two fixes did --
# leaves that pair free to disagree for the next reason. `urls.txt` is what the
# fetch actually iterates, so counting it is the only count that cannot drift
# from what the fetch produces.
cut -f2 "$WORK/hrefs.tsv" | sort -u > "$WORK/urls.txt"
N_URLS=$(count_lines "$WORK/urls.txt")

# One id must resolve to exactly one URL. It does today, but nothing in the
# published collection enforces it -- a duplicated item link would give one id
# two hrefs, which no amount of deduping the ids can reach because the
# duplication is on the href side. Reconciled explicitly rather than assumed,
# and reported as what it is rather than surfacing later as a phantom fetch
# shortfall.
if [ "$N_URLS" -ne "$N_FETCH_IDS" ]; then
  echo "ERROR: $N_FETCH_IDS id(s) resolved to $N_URLS distinct URL(s)." >&2
  echo "       The published collection.json has duplicate or missing item links." >&2
  exit 1
fi
# And the rows, not only the distinct URLs: an item link duplicated with an
# IDENTICAL href collapses under `sort -u` above and passes. --drift/--verify
# would refuse it anyway (published_digests), but --all and --ids-file would
# register everything first and only then trip over it in verify-serving -- a
# successful write reported as a crash, on every rerun. Refused here, before
# anything is fetched or written, in every mode alike.
N_HREF_ROWS=$(count_lines "$WORK/hrefs.tsv")
if [ "$N_HREF_ROWS" -ne "$N_FETCH_IDS" ]; then
  echo "ERROR: $N_FETCH_IDS id(s) have $N_HREF_ROWS item link(s) between them." >&2
  echo "       The published collection.json links an item more than once." >&2
  exit 1
fi

if [ "$MODE" = "all" ] || [ "$MODE" = "ids-file" ]; then
  N_TODO=$(count_lines "$WORK/todo.txt")
  echo "to register: $N_TODO"
  if [ "$DRYRUN" -eq 1 ]; then
    echo "[dryrun] would fetch $N_TODO item(s) and upsert them to $DB on $HOST"
    echo "[dryrun] first 3:"
    head -3 "$WORK/hrefs.tsv" | sed 's/^/  /'
    exit 0
  fi
fi

# Probe before the expensive stage — a dead host otherwise surfaces only after
# a multi-minute fetch, which is the same silent-after-success shape as ARG_MAX.
# Every mode that can write, --drift included: its fetch is the whole catalogue.
if [ "$MODE" != "verify" ] && [ "$DRYRUN" -eq 0 ]; then
  if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" true 2>/dev/null; then
    echo "ERROR: cannot reach $HOST. If the tailnet is down, try the reserved IP:" >&2
    echo "       STAC_HOST=root@146.190.12.8 $0 --$MODE" >&2
    exit 1
  fi
fi

# --- fetch -------------------------------------------------------------------

FETCH_DIR="$WORK/items"
mkdir -p "$FETCH_DIR"

# In one Python process with a thread pool, not one curl per item: the shell
# loop this replaced took ~45 min for the whole catalogue, and since #45
# --verify reads every published body. Each worker writes its OWN file, named
# by a hash of the URL, so ids with spaces and parentheses need no quoting
# anywhere downstream; a body counts only once it parses and is renamed into
# place, so a failed fetch never leaves a file that counts as present.
echo "fetching $N_URLS item JSON(s) with $JOBS workers ..."
: > "$WORK/failed.txt"
# Failures are collected, not aborted on: the fetcher's exit status would
# conflate "one transient 500" with "the bucket is gone", and the count guard
# below is the real gate. stderr goes to a file, never /dev/null: it carries
# the only diagnostic for each URL that failed.
set +e
"$PY" scripts/register_manifest.py fetch-bodies \
  --urls-file "$WORK/urls.txt" --out-dir "$FETCH_DIR" \
  --failed-out "$WORK/failed.txt" --workers "$JOBS" 2>"$WORK/fetch_stderr.txt"
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

# --- what differs (--drift / --verify) ----------------------------------------

COLL_STATE=""
if [ "$MODE" = "drift" ] || [ "$MODE" = "verify" ]; then
  echo "comparing every body with the API (full-body keyset paging; ~6 min at full scale) ..."
  "$PY" scripts/register_manifest.py diff \
    --collection-file "$WORK/collection.json" \
    --collection-id "$COLLECTION_ID" \
    --api "$API" \
    --fetch-dir "$FETCH_DIR" \
    --missing-out "$WORK/missing.txt" \
    --orphaned-out "$WORK/orphaned.txt" \
    --changed-out "$WORK/changed.txt"
  # count_lines returns 0 for a MISSING file, which would make an unwritten list
  # read as "nothing to report" -- the gate silently disarmed. Require all three.
  for f in missing orphaned changed; do
    if [ ! -f "$WORK/$f.txt" ]; then
      echo "ERROR: $f list was not written; cannot compare" >&2
      exit 1
    fi
  done
  LC_ALL=C sort -u "$WORK/missing.txt" "$WORK/changed.txt" > "$WORK/todo.txt"

  # The collection's own body: a version bump with no item change is the same
  # defect one object up. An answer on stdout, and anything else is an error --
  # never read as "same".
  COLL_STATE=$("$PY" scripts/register_manifest.py collection-state \
    --collection-file "$WORK/collection.json" \
    --collection-id "$COLLECTION_ID" --api "$API") || COLL_STATE=""
  case "$COLL_STATE" in
    same|changed|missing) ;;
    *) echo "ERROR: could not compare the registered collection with collection.json" >&2; exit 1 ;;
  esac

  N_TODO=$(count_lines "$WORK/todo.txt")
  echo "to register: $N_TODO"
fi

if [ "$MODE" = "verify" ]; then
  # All directions, because the header and the docs promise all of them.
  # `missing` alone would have reported IN SYNC over any number of orphans --
  # registered items with no published link, the drift direction #28 is open
  # about -- and ids alone over any number of stale bodies (#45).
  N_MISSING=$(count_lines "$WORK/missing.txt")
  N_ORPHANED=$(count_lines "$WORK/orphaned.txt")
  N_CHANGED=$(count_lines "$WORK/changed.txt")
  RC=0
  if [ "$N_MISSING" -gt 0 ]; then
    echo "DRIFT: $N_MISSING published item(s) are not registered" >&2
    head -5 "$WORK/missing.txt" | sed 's/^/  missing:  /' >&2
    RC=1
  fi
  if [ "$N_CHANGED" -gt 0 ]; then
    echo "DRIFT: $N_CHANGED registered item(s) differ from the published body (#45)" >&2
    head -5 "$WORK/changed.txt" | sed 's/^/  changed:  /' >&2
    RC=1
  fi
  if [ "$N_ORPHANED" -gt 0 ]; then
    echo "DRIFT: $N_ORPHANED registered item(s) are no longer published (#28)" >&2
    head -5 "$WORK/orphaned.txt" | sed 's/^/  orphaned: /' >&2
    RC=1
  fi
  case "$COLL_STATE" in
    changed) echo "DRIFT: the registered collection body differs from collection.json" >&2; RC=1 ;;
    missing) echo "DRIFT: the collection is not registered" >&2; RC=1 ;;
  esac
  # Absence of drift is an affirmative result and gets said out loud; a check
  # that prints nothing is indistinguishable from one that never ran.
  if [ "$RC" -eq 0 ]; then
    echo "IN SYNC: $N_PUBLISHED published, all registered with the published body, no orphans"
  fi
  exit "$RC"
fi

if [ "$MODE" = "drift" ]; then
  if [ "$DRYRUN" -eq 1 ]; then
    echo "[dryrun] would upsert $N_TODO item(s) to $DB on $HOST (collection: $COLL_STATE)"
    if [ "$N_TODO" -gt 0 ]; then
      echo "[dryrun] first 5:"
      head -5 "$WORK/todo.txt" | sed 's/^/  /'
    fi
    exit 0
  fi
  if [ "$N_TODO" -eq 0 ] && [ "$COLL_STATE" = "same" ]; then
    echo "nothing to register — already in sync"
    exit 0
  fi
fi

# --- audit, then register: collection first, then items ---------------------

# Only the bodies being registered, by explicit path. --drift fetched the whole
# catalogue to compare it; handing all of that to the audit and the loader would
# register 102k items to change one. Resolved from the same hrefs the fetch
# used, and an id with no fetched body is an error here, not a skipped line.
: > "$WORK/todo_paths.txt"
if [ "$N_TODO" -gt 0 ]; then
  "$PY" scripts/register_manifest.py fetched-paths \
    --hrefs-file "$WORK/hrefs.tsv" --ids-file "$WORK/todo.txt" \
    --fetch-dir "$FETCH_DIR" > "$WORK/todo_paths.txt"

  # Audit every body about to be registered BEFORE anything reaches pgstac --
  # the collection row included. Until #42 this ran after collection_register.sh,
  # so a refused run had already upserted the collection. The id reconciliation
  # above compares STAC_COLLECTION against collection.json's `id`: one field, in
  # one file, out of 102,461. item_register.sh then routes each item by its OWN
  # `collection` field, so a body naming the previous collection upserts into
  # the previous collection successfully, with no error anywhere.
  #
  # --expect ties the count to the todo set rather than to a separately-derived
  # number that could disagree on a healthy run.
  # The asset keys as well as the collection id: for this repo's collection a
  # body can name the right collection and still carry the retired key -- half
  # of the rename, which nothing downstream can see. Which keys, per collection,
  # is resolved at the top of this script (AUDIT_ASSET_ARGS).
  # The `+` form: an empty array under `set -u` is an unbound-variable error on
  # bash 3.2, which is what macOS ships.
  "$PY" scripts/register_manifest.py audit-items \
    --collection-id "$COLLECTION_ID" --expect "$N_TODO" \
    ${AUDIT_ASSET_ARGS[@]+"${AUDIT_ASSET_ARGS[@]}"} < "$WORK/todo_paths.txt"
fi

# The FK ordering. pgstac.items.collection REFERENCES collections(id), so items
# with no collection row fail outright.
./scripts/collection_register.sh "$WORK/collection.json"

if [ "$N_TODO" -gt 0 ]; then
  # Paths on stdin, never argv: 102k filenames is ~6 MB against a ~2 MB ARG_MAX.
  # STAC_COLLECTION makes the same assertion once more inside item_register.sh,
  # on the file it is about to hand pypgstac. Cheap, and the two are not
  # redundant: this one runs even when the audit above is bypassed.
  STAC_COLLECTION="$COLLECTION_ID" ./scripts/item_register.sh < "$WORK/todo_paths.txt"
fi

# --- verify ------------------------------------------------------------------

echo "verifying by set equality and content ..."
# Delegated to register_manifest.py rather than inlined, because the request
# body needs two details an inline heredoc would lose, each of which fails
# silently when omitted. The API's default limit is 10: a body without one
# returns the first 10 ids of however many were asked for, which reads as
# "590 of my 600 items are missing" and fails a verification whose subject was
# fine. And without `collections`, /search answers about every collection on
# the endpoint -- so during #34, when two collections share all 102,460 ids,
# verifying the new one would pass on the old one's rows. Both measured, and
# pinned by tests/test_register_manifest.py.
#
# Content as well as ids (#45): if pgstac ever normalised a field on the way
# in, --drift would re-register the same items every month without converging.
# Comparing what is served with what was sent makes that fail the first time.
COLL_AFTER=$("$PY" scripts/register_manifest.py collection-state \
  --collection-file "$WORK/collection.json" \
  --collection-id "$COLLECTION_ID" --api "$API") || COLL_AFTER=""
if [ "$COLL_AFTER" != "same" ]; then
  echo "FAIL: after registering, the collection reads '${COLL_AFTER:-unreadable}', not 'same'" >&2
  exit 1
fi
echo "OK: the collection is served with the published body"

# Which items to re-read. Normally only the ones just sent. But pgstac serves
# every item HYDRATED against its collection (the collection's item_assets and
# stac_version form a base the stored item was dehydrated against), so a
# collection whose body changed can change how every UNTOUCHED item reads back.
# Checking only the todo set would print DONE over that; the next --verify
# would be the first to see it. So when the collection may have changed and
# every published body is on disk, re-compare the whole catalogue (~6 min):
#   --drift with the collection not 'same' before the write
#   --all, which fetched everything and upserts the collection unconditionally
# --ids-file holds only its own bodies and checks only those: run --verify
# after it if collection.json changed.
FULL_RECHECK=0
if [ "$MODE" = "all" ]; then FULL_RECHECK=1; fi
if [ "$MODE" = "drift" ] && [ "$COLL_STATE" != "same" ]; then FULL_RECHECK=1; fi

if [ "$FULL_RECHECK" -eq 1 ]; then
  echo "re-comparing every published body with the API (the collection was upserted) ..."
  "$PY" scripts/register_manifest.py diff \
    --collection-file "$WORK/collection.json" \
    --collection-id "$COLLECTION_ID" \
    --api "$API" \
    --fetch-dir "$FETCH_DIR" \
    --missing-out "$WORK/after_missing.txt" \
    --orphaned-out "$WORK/after_orphaned.txt" \
    --changed-out "$WORK/after_changed.txt"
  for f in after_missing after_changed; do
    if [ ! -f "$WORK/$f.txt" ]; then
      echo "ERROR: $f list was not written; cannot confirm the registration" >&2
      exit 1
    fi
  done
  N_AFTER_MISSING=$(count_lines "$WORK/after_missing.txt")
  N_AFTER_CHANGED=$(count_lines "$WORK/after_changed.txt")
  if [ "$N_AFTER_MISSING" -gt 0 ] || [ "$N_AFTER_CHANGED" -gt 0 ]; then
    echo "FAIL: after registering, $N_AFTER_MISSING published item(s) are not served and" >&2
    echo "      $N_AFTER_CHANGED are served with a body that differs from the published one" >&2
    head -5 "$WORK/after_missing.txt" | sed 's/^/  missing:  /' >&2
    head -5 "$WORK/after_changed.txt" | sed 's/^/  changed:  /' >&2
    exit 1
  fi
  # Orphans are not this run's failure: nothing here deletes (#28).
  echo "OK: every published item is served with the published body"
elif [ "$N_TODO" -gt 0 ]; then
  "$PY" scripts/register_manifest.py verify-serving \
    --ids-file "$WORK/todo.txt" --collection-id "$COLLECTION_ID" --api "$API" \
    --hrefs-file "$WORK/hrefs.tsv" --fetch-dir "$FETCH_DIR"
fi

echo "DONE: $N_TODO item(s) registered to $COLLECTION_ID"
