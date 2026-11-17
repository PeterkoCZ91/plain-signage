#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# backup.sh — daily backup of the signage data (media + state + telemetry)
#
# Run by cron on the VPS. Packs the data directory into a tar.gz and keeps
# the last KEEP backups. Deliberately simple - enough for a single client.
#
#   SRC=/opt/signage/data DEST=/opt/signage/backups KEEP=14 ./backup.sh
#
# Restore from a backup: deploy/restore.sh (see docs/en/troubleshooting.md).
#
# Installation on the VPS (one-off):
#   scp deploy/backup.sh <vps>:/opt/signage/backup.sh
#   ssh <vps> 'chmod +x /opt/signage/backup.sh && \
#     (crontab -l 2>/dev/null | grep -v signage/backup.sh; \
#      echo "10 3 * * * /opt/signage/backup.sh >> /opt/signage/backup.log 2>&1") | crontab -'
# ---------------------------------------------------------------------------
set -euo pipefail
umask 077

SRC=${SRC:-/opt/signage/data}
DEST=${DEST:-/opt/signage/backups}
KEEP=${KEEP:-14}

fail() { echo "$(date '+%F %T') backup ERROR: $*" >&2; exit 1; }

if [[ ! "$KEEP" =~ ^[0-9]+$ ]] || ((KEEP < 1)); then
    fail "KEEP must be an integer >= 1 (got '$KEEP')"
fi
[ -d "$SRC" ] || fail "source $SRC does not exist"
mkdir -p "$DEST"

# only one backup at a time (cron + manual run)
exec 9>"$DEST/.backup.lock"
flock -n 9 || fail "another backup is already running (lock $DEST/.backup.lock)"

# free space check: we need at least the source size + 10 % margin
need_kb=$(du -sk "$SRC" | cut -f1)
need_kb=$((need_kb + need_kb / 10 + 1024))
avail_kb=$(df -Pk "$DEST" | awk 'NR==2 {print $4}')
((avail_kb >= need_kb)) || fail "not enough space in $DEST: ${avail_kb} kB free, ~${need_kb} kB needed"

STAMP=$(date +%Y%m%d-%H%M%S)
OUT="$DEST/signage-data-$STAMP.tgz"
PART="$OUT.partial"
trap 'rm -f "$PART"' EXIT

# tar returns 1 when a file changed while being read (live server) - only a warning
rc=0
tar -czf "$PART" -C "$(dirname "$SRC")" "$(basename "$SRC")" || rc=$?
((rc <= 1)) || fail "tar exited with code $rc"
((rc == 0)) || echo "$(date '+%F %T') warning: a file changed during the backup (tar rc=1)" >&2

# verify the archive; only then does it become a real backup
tar -tzf "$PART" >/dev/null || fail "archive $PART is corrupted"
[ -s "$PART" ] || fail "archive $PART is empty"
mv -f "$PART" "$OUT"
echo "$(date '+%F %T') backup $OUT ($(du -h "$OUT" | cut -f1))"

# retention without parsing ls: names carry a timestamp, the glob sorts them
# ascending; deletion happens only after a successful new backup
shopt -s nullglob
backups=("$DEST"/signage-data-*.tgz)
if ((${#backups[@]} > KEEP)); then
    for old in "${backups[@]:0:${#backups[@]}-KEEP}"; do
        rm -f -- "$old"
    done
fi
