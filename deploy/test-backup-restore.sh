#!/usr/bin/env bash
# Test of backup.sh + restore.sh in a mktemp directory; no docker, no production data.
#   ./deploy/test-backup-restore.sh
set -euo pipefail

HERE=$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT

export SIGNAGE_RESTORE_SKIP_DOCKER=1
export SIGNAGE_DATA_DIR="$W/data" SIGNAGE_BACKUP_DIR="$W/backups"
export SRC="$W/data" DEST="$W/backups" KEEP=14

PASS=0
ok() { PASS=$((PASS + 1)); echo "  OK   $*"; }
bad() { echo "  FAIL $*" >&2; exit 1; }
expect_fail() { # description, cmd...
    local d=$1; shift
    if "$@" >"$W/out" 2>&1; then bad "$d: expected a failure, exited 0"; fi
    ok "$d (rc!=0)"
}
make_data() {
    rm -rf "$W/data"; mkdir -p "$W/data/tv-media"
    printf '{"items":[{"id":1,"file":"a.mp4"},{"id":2,"file":"b.jpg"}],"schema":1}\n' >"$W/data/tv-state.json"
    head -c 20000 /dev/urandom >"$W/data/tv-media/a.mp4"
    head -c 5000 /dev/urandom >"$W/data/tv-media/b.jpg"
    head -c 300 /dev/urandom >"$W/data/tv-media/c.png"
}
newest() { ls "$W"/backups/signage-data-*.tgz | sort | tail -n1; }

echo "== backup + restore"
make_data
cp -a "$W/data" "$W/orig"
"$HERE/backup.sh" >/dev/null
ARC=$(newest)
echo '{"changed":true}' >"$W/data/tv-state.json"; rm -f "$W/data/tv-media/a.mp4"; echo x >"$W/data/novy.txt"
sleep 1
"$HERE/restore.sh" "$ARC" >"$W/out" 2>&1 || { cat "$W/out"; bad "restore failed"; }
diff -r "$W/orig" "$W/data" && ok "restored data identical to the original"
compgen -G "$W/data.broken-*" >/dev/null && ok "old data moved to data.broken-*"
grep -q changed "$W"/data.broken-*/tv-state.json && ok "data.broken contains the changed (original) data"
grep -q changed "$W/out" && bad "output contains state contents"
ok "output does not print state contents"

echo "== latest"
rm -rf "$W"/data.broken-*
echo '{"a":1}' >"$W/data/tv-state.json"
"$HERE/restore.sh" latest >/dev/null 2>&1
diff -r "$W/orig" "$W/data" && ok "latest restored the newest backup"

echo "== dry-run"
echo '{"a":2}' >"$W/data/tv-state.json"
cp -a "$W/data" "$W/before"
rm -rf "$W"/data.broken-*
"$HERE/restore.sh" --dry-run "$ARC" >"$W/out" 2>&1
diff -r "$W/before" "$W/data" && ok "dry-run did not change the data"
compgen -G "$W/data.broken-*" >/dev/null && bad "dry-run created data.broken"
grep -q 'dry-run' "$W/out" && ok "dry-run printed the plan"

echo "== corrupted archive"
BAD="$W/backups/signage-data-20000101-000000.tgz"
head -c 3000 "$ARC" >"$BAD"
expect_fail "corrupted archive rejected" "$HERE/restore.sh" "$BAD"
diff -r "$W/before" "$W/data" && ok "data untouched after rejecting the corrupted archive"
compgen -G "$W/data.broken-*" >/dev/null && bad "data.broken was created"
rm -f "$BAD"

echo "== archive without tv-state.json"
mkdir -p "$W/nostate/data/tv-media"; echo x >"$W/nostate/data/tv-media/a.mp4"
tar -czf "$W/nostate.tgz" -C "$W/nostate" data
expect_fail "archive without tv-state.json rejected" "$HERE/restore.sh" "$W/nostate.tgz"
grep -q 'tv-state.json' "$W/out" && ok "message mentions tv-state.json"
diff -r "$W/before" "$W/data" && ok "data untouched"

echo "== archive with invalid JSON"
mkdir -p "$W/badjson/data/tv-media"; echo '{rozbite' >"$W/badjson/data/tv-state.json"
tar -czf "$W/badjson.tgz" -C "$W/badjson" data
expect_fail "invalid JSON rejected" "$HERE/restore.sh" "$W/badjson.tgz"
diff -r "$W/before" "$W/data" && ok "data untouched"

echo "== rollback on an error after unpacking (tv-media missing)"
mkdir -p "$W/nomedia/data"; echo '{"ok":1}' >"$W/nomedia/data/tv-state.json"
tar -czf "$W/nomedia.tgz" -C "$W/nomedia" data
expect_fail "archive without tv-media fails verification" "$HERE/restore.sh" "$W/nomedia.tgz"
diff -r "$W/before" "$W/data" && ok "rollback restored the original data"
compgen -G "$W/data.broken-*" >/dev/null && bad "data.broken-* left after the rollback"
compgen -G "$W/.restore-*" >/dev/null && bad "staging directories left after the rollback"
ok "no data.broken-* or leftovers after the rollback"
grep -q 'restoring original' "$W/out" && ok "rollback message"

echo "== rollback without an existing data directory"
rm -rf "$W/data"
expect_fail "fails even when the data did not exist" "$HERE/restore.sh" "$W/nomedia.tgz"
[ ! -e "$W/data" ] && ok "data was not created"

echo "== missing argument / nonexistent file"
expect_fail "no argument" "$HERE/restore.sh"
expect_fail "nonexistent file" "$HERE/restore.sh" "$W/missing.tgz"

echo "ALL OK ($PASS checks)"
