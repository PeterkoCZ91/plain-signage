#!/usr/bin/env bash
set -euo pipefail
umask 022

# Publishes a new version of the Android kiosk (app.plainsignage.kiosk) to the server for a
# controlled update: APK + kiosk.json into $REMOTE_ROOT/data/kiosk/ (in the container
# /data/kiosk). Boxes find the release via GET /api/kiosk/release; installation
# for a specific screen is requested by the admin.

ROOT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)"
SSH_ALIAS="${SIGNAGE_SSH_ALIAS:-}"
REMOTE_ROOT="${SIGNAGE_REMOTE_ROOT:-/opt/signage}"
REMOTE_DIR="$REMOTE_ROOT/data/kiosk"
PACKAGE="app.plainsignage.kiosk"
APK="$ROOT_DIR/kiosk-app/build/signage-kiosk.apk"
DRY_RUN=0
FORCE=0

usage() {
    cat <<'EOF'
Usage: deploy/tools/publish-kiosk.sh [--dry-run] [--force] [path/to.apk]

Without a path kiosk-app/build/signage-kiosk.apk is used.
  --dry-run  only verifies the APK and prints kiosk.json and the target, does not touch the server
  --force    publish even if the server already has a higher (or the same, different) versionCode

Variables:
  SIGNAGE_SSH_ALIAS   SSH host alias (REQUIRED, no default)
  SIGNAGE_REMOTE_ROOT Remote root (default /opt/signage)
EOF
}

die() { echo "ERROR: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --force) FORCE=1 ;;
        -h|--help) usage; exit 0 ;;
        -*) usage >&2; exit 2 ;;
        *) APK="$1" ;;
    esac
    shift
done

if [ -z "$SSH_ALIAS" ]; then
    if [ "$DRY_RUN" = 1 ]; then
        SSH_ALIAS="<SIGNAGE_SSH_ALIAS>"
    else
        echo "SIGNAGE_SSH_ALIAS is required: the name of your SSH host (an alias from ~/.ssh/config)." >&2
        echo "Example: SIGNAGE_SSH_ALIAS=my-server deploy/tools/publish-kiosk.sh" >&2
        exit 2
    fi
fi

[ -f "$APK" ] || die "APK does not exist: $APK"

# tool from PATH, otherwise the newest build-tools in the Android SDK
find_tool() {
    local name=$1 sdk dir found=""
    if command -v "$name" >/dev/null 2>&1; then
        command -v "$name"
        return 0
    fi
    for sdk in "${ANDROID_HOME:-}" "${ANDROID_SDK_ROOT:-}" "$HOME/Android/Sdk"; do
        if [ -z "$sdk" ] || [ ! -d "$sdk/build-tools" ]; then
            continue
        fi
        dir=$(find "$sdk/build-tools" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null \
              | sort -V | tail -n 1)
        if [ -n "$dir" ] && [ -x "$sdk/build-tools/$dir/$name" ]; then
            found="$sdk/build-tools/$dir/$name"
            break
        fi
    done
    [ -n "$found" ] || return 1
    echo "$found"
}

AAPT2=$(find_tool aapt2) || die "aapt2 not found (neither in PATH nor in \$HOME/Android/Sdk/build-tools)"
APKSIGNER=$(find_tool apksigner) || die "apksigner not found (neither in PATH nor in \$HOME/Android/Sdk/build-tools)"

"$APKSIGNER" verify "$APK" >/dev/null || die "apksigner verify failed: $APK"

BADGING=$("$AAPT2" dump badging "$APK" | sed -n '1p')
case "$BADGING" in
    "package: "*) ;;
    *) die "aapt2 did not return a package: line ($BADGING)" ;;
esac
attr() { printf '%s\n' "$BADGING" | sed -n "s/.* $1='\\([^']*\\)'.*/\\1/p"; }
PKG=$(attr name)
VERSION_CODE=$(attr versionCode)
VERSION_NAME=$(attr versionName)

[ "$PKG" = "$PACKAGE" ] || die "wrong package: '$PKG' (expected $PACKAGE)"
[[ "$VERSION_CODE" =~ ^[1-9][0-9]{0,9}$ ]] || die "invalid versionCode: '$VERSION_CODE'"
SHA256=$(sha256sum "$APK" | cut -d' ' -f1)
SIZE=$(stat -c %s "$APK")

# JSON via python (versionName escaping); same rules as the server-side validation
JSON=$(python3 - "$VERSION_CODE" "$VERSION_NAME" "$SHA256" "$SIZE" <<'PY'
import json, sys, unicodedata
code, name, sha, size = int(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4])
if not 0 < code <= 2 ** 31 - 1:
    sys.exit("versionCode out of range")
if not name or len(name) > 32 or any(c in "<>" or unicodedata.category(c).startswith("C") for c in name):
    sys.exit("invalid versionName (1-32 characters, no < > or control characters): %r" % name)
print(json.dumps({"versionCode": code, "versionName": name, "sha256": sha, "size": size}))
PY
) || die "cannot build kiosk.json"

echo "APK:    $APK"
echo "Target: $SSH_ALIAS:$REMOTE_DIR/"
echo "JSON:   $JSON"

if [ "$DRY_RUN" = 1 ]; then
    echo "--dry-run: nothing was uploaded"
    exit 0
fi

# What is already on the server? We do not silently overwrite a lower version
# (boxes would never "update" back) and neither the same versionCode with a different APK.
REMOTE_JSON=$(ssh "$SSH_ALIAS" sh -s -- "$REMOTE_DIR/kiosk.json" <<'REMOTE'
if [ -f "$1" ]; then cat "$1"; fi
REMOTE
) || die "server $SSH_ALIAS does not respond (ssh)"
if [ -n "$REMOTE_JSON" ]; then
    read -r REMOTE_CODE REMOTE_SHA < <(printf '%s' "$REMOTE_JSON" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print(int(d.get("versionCode", 0)), str(d.get("sha256", "-")) or "-")
except Exception:
    print(0, "-")
') || { REMOTE_CODE=0; REMOTE_SHA=-; }
    echo "Server: versionCode $REMOTE_CODE"
    if [ "$REMOTE_CODE" = "$VERSION_CODE" ] && [ "$REMOTE_SHA" = "$SHA256" ]; then
        echo "The server already has exactly this APK, nothing to do."
        exit 0
    fi
    if [ "$REMOTE_CODE" -ge "$VERSION_CODE" ]; then
        echo "WARNING: the server has versionCode $REMOTE_CODE >= $VERSION_CODE (new APK)." >&2
        if [ "$FORCE" != 1 ]; then
            die "not publishing; raise versionCode or add --force"
        fi
        echo "--force: overwriting." >&2
    fi
fi

# Atomic: upload under temporary names in the same directory, verify, then mv (APK before
# JSON - the server treats a release as valid only if the size in the JSON matches the APK).
TAG="$(date +%Y%m%d%H%M%S)-$$"
TMP_APK="$REMOTE_DIR/.signage-kiosk.apk.tmp-$TAG"
TMP_JSON="$REMOTE_DIR/.kiosk.json.tmp-$TAG"
LOCAL_JSON=$(mktemp)
trap 'rm -f "$LOCAL_JSON"' EXIT
printf '%s\n' "$JSON" > "$LOCAL_JSON"

ssh "$SSH_ALIAS" sh -s -- "$REMOTE_DIR" <<'REMOTE'
mkdir -p "$1"
REMOTE
scp -q "$APK" "$SSH_ALIAS:$TMP_APK"
scp -q "$LOCAL_JSON" "$SSH_ALIAS:$TMP_JSON"
ssh "$SSH_ALIAS" sh -s -- "$TMP_APK" "$TMP_JSON" "$REMOTE_DIR" "$SHA256" <<'REMOTE'
set -eu
tmp_apk=$1 tmp_json=$2 dir=$3 sha=$4
cleanup() { rm -f "$tmp_apk" "$tmp_json"; }
trap cleanup EXIT
got=$(sha256sum "$tmp_apk" | cut -d' ' -f1)
if [ "$got" != "$sha" ]; then
    echo "ERROR: sha256 of the uploaded APK does not match ($got)" >&2
    exit 1
fi
chmod 644 "$tmp_apk" "$tmp_json"
mv -f "$tmp_apk" "$dir/signage-kiosk.apk"
mv -f "$tmp_json" "$dir/kiosk.json"
REMOTE

echo "Published: kiosk $VERSION_NAME (versionCode $VERSION_CODE) -> $SSH_ALIAS:$REMOTE_DIR/"
echo "Request the update for a screen in the admin (\"Screen availability (24 h)\" -> \"Update kiosk\")."
