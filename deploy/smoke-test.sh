#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# smoke-test.sh — quick smoke test of the live signage service
#
# Verifies that the running instance answers the key endpoints correctly:
# health, security headers, basic-auth protection, playlist, settings
# round trip, static assets and rate limit. It deletes nothing and changes
# nothing permanently (settings are returned to their original state).
#
# Usage (the password is PASSED VIA ENV, it is never in this script):
#   APASS='secretpassword' ./deploy/smoke-test.sh
#   BASE=https://signage.example.com AUSER=admin APASS='...' ./deploy/smoke-test.sh
# ---------------------------------------------------------------------------
set -uo pipefail
umask 077

# --- Configuration from the environment (with defaults) ----------------------------------
BASE=${BASE:-}                                   # base URL of the live service (required)
AUSER=${AUSER:-admin}                            # admin login (basic auth)
if [ -z "$BASE" ]; then
    echo "ERROR: BASE (service URL) is missing - e.g. BASE=https://signage.example.com $0" >&2
    exit 2
fi
# NOTE: the password is NOT hardcoded; without APASS the script exits with an error:
#   APASS='real-password' ./deploy/smoke-test.sh
APASS=${APASS:-}
if [ -z "$APASS" ]; then
    echo "ERROR: APASS (admin password) is missing - pass it via env, e.g. APASS='...' $0" >&2
    exit 2
fi
# The rate-limit test deliberately produces 401/429 and may block an admin on
# the same IP for a while. Enable it only from an isolated runner: SKIP_RATELIMIT=0.
SKIP_RATELIMIT=${SKIP_RATELIMIT:-1}

# curl with a short timeout so the test never hangs forever
CURL="curl -s --max-time 15"
# Authorized call: credentials go through a -K file (chmod 600), not through
# command arguments, so they are not visible in `ps`. The file is created below, after mktemp.

# --- Colors (only on a terminal) ---------------------------------------
if [ -t 1 ]; then
    GREEN=$'\033[32m'; RED=$'\033[31m'; DIM=$'\033[2m'; NC=$'\033[0m'
else
    GREEN=""; RED=""; DIM=""; NC=""
fi

# --- Temporary files for response bodies and headers --------------------------
TMPDIR=$(mktemp -d "${TMPDIR:-/tmp}/signage-smoke.XXXXXX")
BODY="$TMPDIR/body"     # response body is stored here
HDR="$TMPDIR/hdr"       # response headers go here
trap 'rm -rf "$TMPDIR"' EXIT

# curl config with the credentials (escape quotes and backslashes per curl -K syntax)
write_authcfg() {   # write_authcfg file user password
    local u=$2 p=$3 cred
    cred="$u:$p"
    cred=${cred//\\/\\\\}
    cred=${cred//\"/\\\"}
    printf 'user = "%s"\n' "$cred" > "$1"
}
AUTHCFG="$TMPDIR/auth.cfg"
write_authcfg "$AUTHCFG" "$AUSER" "$APASS"
CURLA="$CURL -K $AUTHCFG"

# --- Counters + check function ----------------------------------------------
TOTAL=0
FAIL=0

# check "test name" command...  → runs the command, prints PASS/FAIL
check() {
    local name="$1"; shift
    TOTAL=$((TOTAL + 1))
    if "$@"; then
        printf "  ${GREEN}PASS${NC}  %s\n" "$name"
    else
        printf "  ${RED}FAIL${NC}  %s\n" "$name"
        FAIL=$((FAIL + 1))
    fi
}

# ---------------------------------------------------------------------------
# Individual tests - each returns 0 (OK) or 1 (failure) and on failure prints
# a short diagnostic indented by four spaces.
# ---------------------------------------------------------------------------

# 1) GET /api/health → 200 + required keys in the JSON
t_health() {
    local code
    code=$($CURL -o "$BODY" -w '%{http_code}' "$BASE/api/health")
    [ "$code" = "200" ] || { echo "    HTTP $code"; return 1; }
    python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
assert d.get("ok") is True, "ok != true"
for k in ("version", "max_file_mb", "max_items", "max_total_mb", "alerts_enabled"):
    assert k in d, "missing key %s" % k
' "$BODY" 2>&1 | sed 's/^/    /' >&2
    return "${PIPESTATUS[0]}"
}

# 2) GET /tv/ → 200 + HTTPS security headers (provided by nginx)
t_tv_headers() {
    local code miss=""
    code=$($CURL -D "$HDR" -o /dev/null -w '%{http_code}' "$BASE/tv/")
    [ "$code" = "200" ] || { echo "    HTTP $code"; return 1; }
    local h
    for h in strict-transport-security x-frame-options x-content-type-options; do
        grep -qi "^$h:" "$HDR" || miss="$miss $h"
    done
    [ -z "$miss" ] || { echo "    missing headers:$miss"; return 1; }
}

# 3) GET /admin  without password → 401,  with password → 200
t_admin_auth() {
    local c401 c200
    c401=$($CURL -o /dev/null -w '%{http_code}' "$BASE/admin")
    [ "$c401" = "401" ] || { echo "    without password expected 401, got $c401"; return 1; }
    c200=$($CURLA -o /dev/null -w '%{http_code}' "$BASE/admin")
    [ "$c200" = "200" ] || { echo "    with password expected 200, got $c200 (wrong AUSER/APASS?)"; return 1; }
}

# 4) GET /api/devices  without password → 401,  with password → 200
t_devices_auth() {
    local c401 c200
    c401=$($CURL -o /dev/null -w '%{http_code}' "$BASE/api/devices")
    [ "$c401" = "401" ] || { echo "    without password expected 401, got $c401"; return 1; }
    c200=$($CURLA -o /dev/null -w '%{http_code}' "$BASE/api/devices")
    [ "$c200" = "200" ] || { echo "    with password expected 200, got $c200"; return 1; }
}

# 5) GET /api/tv/default/playlist → 200 + JSON structure (+ item keys)
t_playlist() {
    local code
    code=$($CURL -o "$BODY" -w '%{http_code}' "$BASE/api/tv/default/playlist")
    [ "$code" = "200" ] || { echo "    HTTP $code"; return 1; }
    python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
for k in ("version", "items", "settings"):
    assert k in d, "missing key %s" % k
assert d["settings"].get("transition") in ("fade", "cut"), "transition is not valid"
assert "qrHandle" in d["settings"] and "qrLabel" in d["settings"], "missing qrHandle/qrLabel"
assert isinstance(d["settings"].get("qrCards"), list), "missing qrCards"
assert isinstance(d["items"], list), "items is not an array"
for it in d["items"]:                      # if there are items, check the keys
    for k in ("enabled", "sound", "from", "until"):
        assert k in it, "item lacks %s" % k
' "$BODY" 2>&1 | sed 's/^/    /' >&2
    return "${PIPESTATUS[0]}"
}

# Helper: sets settings.qrCorner (true/false) via an authorized POST,
# prints the HTTP code to stdout.
_post_qr() {
    $CURLA -o /dev/null -w '%{http_code}' \
        -H 'Content-Type: application/json' \
        --data "{\"device\":\"default\",\"qrCorner\":$1}" \
        "$BASE/api/settings"
}

# Helper: verifies that settings.qrCorner in the playlist equals the expected value
_check_qr() {
    $CURL "$BASE/api/tv/default/playlist" | python3 -c '
import json, sys
d = json.load(sys.stdin)
exp = sys.argv[1] == "true"
got = bool(d.get("settings", {}).get("qrCorner"))
assert got is exp, "qrCorner=%s, expected %s" % (got, exp)
' "$1"
}

# 6) Settings round trip: qrCorner true → verify → false → verify (ends at false)
t_settings_roundtrip() {
    local c
    c=$(_post_qr true)
    if [ "$c" != "200" ]; then echo "    POST qrCorner=true -> $c"; _post_qr false >/dev/null; return 1; fi
    if ! _check_qr true; then echo "    qrCorner did not stay true"; _post_qr false >/dev/null; return 1; fi
    c=$(_post_qr false)
    if [ "$c" != "200" ]; then echo "    POST qrCorner=false -> $c"; return 1; fi
    if ! _check_qr false; then echo "    qrCorner did not stay false"; return 1; fi
}

# 7) GET /robots.txt → contains "Disallow: /"
t_robots() {
    $CURL "$BASE/robots.txt" | grep -qi '^Disallow: /' || { echo "    missing 'Disallow: /'"; return 1; }
}

# 8) GET /assets/manifest.webmanifest → 200 + Content-Type contains "manifest"
t_manifest() {
    local code
    code=$($CURL -D "$HDR" -o /dev/null -w '%{http_code}' "$BASE/assets/manifest.webmanifest")
    [ "$code" = "200" ] || { echo "    HTTP $code"; return 1; }
    grep -qi '^content-type:.*manifest' "$HDR" || { echo "    Content-Type does not contain 'manifest'"; return 1; }
}

# 9) Static icons → 200
t_assets_png() {
    local a code
    for a in apple-touch-icon.png default-qr.png; do
        code=$($CURL -o /dev/null -w '%{http_code}' "$BASE/assets/$a")
        [ "$code" = "200" ] || { echo "    /assets/$a -> $code"; return 1; }
    done
}

# 10) Service worker and its locally bundled dependencies → 200
t_offline_assets() {
    local p code
    for p in /sw.js /assets/workbox-core.prod.js /assets/workbox-strategies.prod.js /assets/workbox-range-requests.prod.js; do
        code=$($CURL -o /dev/null -w '%{http_code}' "$BASE$p")
        [ "$code" = "200" ] || { echo "    $p -> HTTP $code"; return 1; }
    done
}

# 11) Rate-limit: 25 quick GET /admin with a wrong password → at least one 429
t_ratelimit() {
    local i code hit=0
    for i in $(seq 1 25); do
        write_authcfg "$TMPDIR/bad.cfg" "$AUSER" "wrong_password_$i"
        code=$($CURL -K "$TMPDIR/bad.cfg" -o /dev/null -w '%{http_code}' "$BASE/admin")
        [ "$code" = "429" ] && hit=1
    done
    [ "$hit" = "1" ] || { echo "    none of 25 attempts returned 429 (rate limit not working?)"; return 1; }
}

# 12) Availability history is protected and returns hourly buckets without the user agent
t_availability() {
    local c401 code invalid
    c401=$($CURL -o /dev/null -w '%{http_code}' "$BASE/api/devices/default/availability?hours=24")
    [ "$c401" = "401" ] || { echo "    without password expected 401, got $c401"; return 1; }
    code=$($CURLA -o "$BODY" -w '%{http_code}' \
        "$BASE/api/devices/default/availability?hours=24")
    [ "$code" = "200" ] || { echo "    with password expected 200, got $code"; return 1; }
    python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
assert d.get("device") == "default", "wrong device"
assert d.get("bucket_seconds") == 3600, "expected an hourly bucket"
assert len(d.get("buckets", [])) == 24, "expected 24 buckets"
assert all(x.get("state") in ("seen", "gap", "unknown") for x in d["buckets"])
assert "agent" not in d, "user agent must not leak from the API"
' "$BODY" 2>&1 | sed 's/^/    /' >&2 || return 1
    invalid=$($CURLA -o /dev/null -w '%{http_code}' \
        "$BASE/api/devices/default/availability?hours=0")
    [ "$invalid" = "400" ] || { echo "    hours=0 expected 400, got $invalid"; return 1; }
}

# ---------------------------------------------------------------------------
# Test run
# ---------------------------------------------------------------------------
printf "%sSignage service smoke test%s  →  %s\n" "$DIM" "$NC" "$BASE"
echo

check "1. /api/health (200 + keys)"                 t_health
check "2. /tv/ security headers (HTTPS)"            t_tv_headers
check "3. /admin  401 without password / 200 with password"     t_admin_auth
check "4. /api/devices  401 without / 200 with password"     t_devices_auth
check "5. /api/tv/default/playlist (structure)"     t_playlist
check "6. /api/settings qrCorner round trip"        t_settings_roundtrip
check "7. /robots.txt (Disallow: /)"                 t_robots
check "8. /assets/manifest.webmanifest"              t_manifest
check "9. /assets icons (png 200)"                   t_assets_png
check "10. offline assets (service worker + Workbox)" t_offline_assets
if [ "$SKIP_RATELIMIT" = "0" ]; then
    check "11. rate-limit (25x /admin -> 429)"       t_ratelimit
else
    printf "  %sSKIP%s  11. rate-limit (set SKIP_RATELIMIT=0 for an isolated run)\n" "$DIM" "$NC"
fi
check "12. availability history (auth + structure)"   t_availability

# --- Summary ----------------------------------------------------------------
OK=$((TOTAL - FAIL))
echo
if [ "$FAIL" -gt 0 ]; then
    printf "Total: %s%d OK%s, %s%d FAIL%s\n" "$GREEN" "$OK" "$NC" "$RED" "$FAIL" "$NC"
    exit 1
else
    printf "Total: %s%d OK%s, %d FAIL\n" "$GREEN" "$OK" "$NC" "$FAIL"
    exit 0
fi
