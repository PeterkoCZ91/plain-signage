#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# restore.sh — safe restore of the signage data from a backup made by backup.sh
#
#   SIGNAGE_DATA_DIR=/opt/signage/data SIGNAGE_BACKUP_DIR=/opt/signage/backups \
#     ./restore.sh [--dry-run] <backup-file.tgz | latest>
#
# Env (defaults = production, same as backup.sh and deploy.sh):
#   SIGNAGE_DATA_DIR             data directory (contains tv-state.json, tv-media/)
#   SIGNAGE_BACKUP_DIR           directory holding the signage-data-*.tgz backups
#   SIGNAGE_CONTAINER            docker container name (default signage)
#   SIGNAGE_RESTORE_SKIP_DOCKER=1  do not touch docker at all (tests)
#
# Procedure: verifies the archive (tar -tzf, tv-state.json, valid JSON, disk
# space), stops the container, MOVES the existing data to data.broken-<stamp>
# (never deletes it), unpacks, verifies the result and starts the container.
# On error it puts the original directory back and exits non-zero. State
# contents and credentials are never printed.
# ---------------------------------------------------------------------------
set -euo pipefail
umask 077

DATA_DIR=${SIGNAGE_DATA_DIR:-/opt/signage/data}
BACKUP_DIR=${SIGNAGE_BACKUP_DIR:-/opt/signage/backups}
CONTAINER=${SIGNAGE_CONTAINER:-signage}
SKIP_DOCKER=${SIGNAGE_RESTORE_SKIP_DOCKER:-0}

fail() { echo "$(date '+%F %T') restore ERROR: $*" >&2; exit 1; }
info() { echo "$(date '+%F %T') $*"; }

DRY=0
ARG=""
for a in "$@"; do
    case "$a" in
        --dry-run) DRY=1 ;;
        -h | --help) sed -n '2,19p' "$0"; exit 0 ;;
        -*) fail "unknown option $a" ;;
        *) [ -z "$ARG" ] || fail "too many arguments, expected a single backup file or 'latest'"; ARG=$a ;;
    esac
done
[ -n "$ARG" ] || fail "missing argument: backup file or 'latest'"

DATA_DIR=${DATA_DIR%/}
[ -n "$DATA_DIR" ] && [ "$DATA_DIR" != "/" ] || fail "SIGNAGE_DATA_DIR is empty or /"
PARENT=$(dirname "$DATA_DIR")
[ -d "$PARENT" ] || fail "parent directory $PARENT does not exist"

# --- archive selection ---------------------------------------------------------
if [ "$ARG" = latest ]; then
    shopt -s nullglob
    cands=("$BACKUP_DIR"/signage-data-*.tgz)
    shopt -u nullglob
    ((${#cands[@]} > 0)) || fail "no signage-data-*.tgz backup in $BACKUP_DIR"
    ARCHIVE=${cands[${#cands[@]}-1]}
else
    ARCHIVE=$ARG
fi
[ -f "$ARCHIVE" ] || fail "backup file $ARCHIVE does not exist"
[ -s "$ARCHIVE" ] || fail "backup file $ARCHIVE is empty"

# only one restore/backup at a time (same lock as backup.sh)
if [ -d "$BACKUP_DIR" ]; then
    exec 9>"$BACKUP_DIR/.backup.lock"
    flock -n 9 || fail "a backup or another restore is running (lock $BACKUP_DIR/.backup.lock)"
fi

# --- archive verification (nothing is changed yet) ---------------------------------
LIST=$(mktemp)
STAGE=""
trap 'rm -f "$LIST"' EXIT
tar -tzf "$ARCHIVE" >"$LIST" 2>/dev/null || fail "archive $ARCHIVE is corrupted (tar -tzf)"
[ -s "$LIST" ] || fail "archive $ARCHIVE is empty"

TOP=$(head -n1 "$LIST" | cut -d/ -f1)
[ -n "$TOP" ] && [ "$TOP" != "." ] && [ "$TOP" != ".." ] || fail "archive does not have the expected structure (a single root directory)"
if grep -qE '(^|/)\.\.(/|$)|^/' "$LIST"; then
    fail "archive contains unsafe paths (.. or absolute)"
fi
if grep -qvE "^${TOP//./\\.}(/|$)" "$LIST"; then
    fail "archive contains more root entries than '$TOP/'"
fi
grep -qxF "$TOP/tv-state.json" "$LIST" || fail "archive does not contain $TOP/tv-state.json"

# tv-state.json must be valid JSON (contents are not printed)
tar -xzOf "$ARCHIVE" "$TOP/tv-state.json" 2>/dev/null \
    | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null \
    || fail "tv-state.json in the archive is not valid JSON"

# --- disk space: unpacked size + 10 % (the old data is only moved) --
need_kb=$(tar -tzvf "$ARCHIVE" | awk '{s+=$3} END {printf "%d", s/1024}')
need_kb=$((need_kb + need_kb / 10 + 1024))
avail_kb=$(df -Pk "$PARENT" | awk 'NR==2 {print $4}')
((avail_kb >= need_kb)) || fail "not enough space in $PARENT: ${avail_kb} kB free, ~${need_kb} kB needed"

# --- docker ----------------------------------------------------------------
STOPPED=0
docker_running() {
    [ "$SKIP_DOCKER" != 1 ] && command -v docker >/dev/null 2>&1 \
        && [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true)" = true ]
}

STAMP=$(date +%Y%m%d-%H%M%S)
BROKEN="$PARENT/$(basename "$DATA_DIR").broken-$STAMP"
n=1
while [ -e "$BROKEN" ]; do BROKEN="$PARENT/$(basename "$DATA_DIR").broken-$STAMP-$n"; n=$((n + 1)); done

if ((DRY)); then
    info "dry-run: archive $ARCHIVE verified (tar -tzf, tv-state.json, valid JSON)"
    info "dry-run: unpacked ~${need_kb} kB, ${avail_kb} kB free in $PARENT"
    if docker_running; then info "dry-run: would stop container $CONTAINER"; else info "dry-run: would not touch docker"; fi
    if [ -e "$DATA_DIR" ]; then info "dry-run: would move $DATA_DIR -> $BROKEN"; else info "dry-run: $DATA_DIR does not exist, nothing to move"; fi
    info "dry-run: would unpack $ARCHIVE into $PARENT, verify tv-state.json and $TOP/tv-media, start the container"
    exit 0
fi

# --- the actual restore, with rollback -------------------------------------------
MOVED=0
DONE=0
rollback() {
    local rc=$?
    trap - EXIT ERR
    rm -f "$LIST"
    if ((DONE == 0)); then
        echo "$(date '+%F %T') restore ERROR: failed (code $rc), restoring original state" >&2
        [ -z "$STAGE" ] || [ ! -d "$STAGE" ] || rm -rf -- "$STAGE"
        if ((MOVED)); then
            if [ -e "$DATA_DIR" ]; then
                # this is only our own unpacked copy (the archive stays); the original data is never deleted
                mv -- "$DATA_DIR" "$PARENT/.restore-failed-$STAMP" \
                    && rm -rf -- "$PARENT/.restore-failed-$STAMP" \
                    || echo "ERROR: cannot move the unpacked data out of $DATA_DIR" >&2
            fi
            mv -- "$BROKEN" "$DATA_DIR" \
                && echo "original data restored to $DATA_DIR" >&2 \
                || echo "CRITICAL: the original data is in $BROKEN, put it back manually" >&2
        fi
        if ((STOPPED)); then
            docker start "$CONTAINER" >/dev/null 2>&1 \
                || echo "WARNING: could not start container $CONTAINER" >&2
        fi
        exit "$((rc == 0 ? 1 : rc))"
    fi
}
trap rollback EXIT ERR

if docker_running; then
    info "stopping container $CONTAINER"
    docker stop "$CONTAINER" >/dev/null || fail "could not stop container $CONTAINER"
    STOPPED=1
fi

# unpack into a staging directory next to the data (same filesystem => mv is atomic)
STAGE=$(mktemp -d "$PARENT/.restore-staging-XXXXXX")
tar -xzf "$ARCHIVE" -C "$STAGE" || fail "unpacking the archive failed"
[ -d "$STAGE/$TOP" ] || fail "$TOP/ missing after unpacking"

if [ -e "$DATA_DIR" ]; then
    mv -- "$DATA_DIR" "$BROKEN" || fail "cannot move $DATA_DIR"
    MOVED=1
    info "old data: $BROKEN"
fi
mv -- "$STAGE/$TOP" "$DATA_DIR" || fail "cannot place the restored data into $DATA_DIR"
rmdir "$STAGE" 2>/dev/null || true
STAGE=""

# result verification
python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$DATA_DIR/tv-state.json" 2>/dev/null \
    || fail "tv-state.json is not valid JSON after the restore"
[ -d "$DATA_DIR/tv-media" ] || fail "media directory $DATA_DIR/tv-media missing after the restore"

DONE=1
trap 'rm -f "$LIST"' EXIT
trap - ERR
if ((STOPPED)); then
    info "starting container $CONTAINER"
    docker start "$CONTAINER" >/dev/null || echo "WARNING: could not start container $CONTAINER, check docker ps -a" >&2
fi
info "restore finished from $ARCHIVE"
[ "$MOVED" != 1 ] || info "original data kept in $BROKEN (delete only after verifying /api/health)"
