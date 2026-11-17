#!/usr/bin/env bash
set -euo pipefail
umask 077

# Safe deploy of the signage image to the VPS. Credentials are read from the
# existing container on the server and never passed as an argument of a local command.

ROOT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
SSH_ALIAS="${SIGNAGE_SSH_ALIAS:-}"
REMOTE_ROOT="${SIGNAGE_REMOTE_ROOT:-/opt/signage}"
CONTAINER="${SIGNAGE_CONTAINER:-signage}"
HEALTH_URL="${SIGNAGE_HEALTH_URL:-http://172.17.0.1:8090/api/health}"
AUTH_URL="${SIGNAGE_AUTH_URL:-http://172.17.0.1:8090/admin}"
PLAYLIST_URL="${SIGNAGE_PLAYLIST_URL:-http://172.17.0.1:8090/api/tv/default/playlist}"
CANARY_PORT="${SIGNAGE_CANARY_PORT:-18090}"
SKIP_TESTS="${SIGNAGE_SKIP_TESTS:-0}"
KEEP_RELEASES="${SIGNAGE_KEEP_RELEASES:-5}"
PIDS_LIMIT="${SIGNAGE_PIDS_LIMIT:-256}"
CAP_DROP="${SIGNAGE_CAP_DROP:-0}"
REQUIRE_CLEAN="${SIGNAGE_REQUIRE_CLEAN:-0}"
SMOKE="${SIGNAGE_SMOKE:-0}"
SMOKE_ROLLBACK="${SIGNAGE_SMOKE_ROLLBACK:-1}"

usage() {
    cat <<'EOF'
Usage: deploy/deploy.sh [--dry-run] [--skip-tests]

Variables:
  SIGNAGE_SSH_ALIAS   SSH host alias (REQUIRED, no default)
  SIGNAGE_REMOTE_ROOT Remote root (default /opt/signage)
  SIGNAGE_HEALTH_URL  Internal health URL
  SIGNAGE_AUTH_URL    Internal URL of the protected admin
  SIGNAGE_PLAYLIST_URL Internal playlist URL
  SIGNAGE_CANARY_PORT  Local port for the canary (default 18090)
  SIGNAGE_BUILD       Build version; otherwise timestamp-gitSHA[-dirty]
  SIGNAGE_KEEP_RELEASES How many recent images/release directories to keep (default 5)
  SIGNAGE_PIDS_LIMIT  container --pids-limit (default 256)
  SIGNAGE_CAP_DROP    1 = add --cap-drop ALL (default 0, verify on the VPS)
  SIGNAGE_REQUIRE_CLEAN 1 = fail on a dirty working tree in deploy/
  SIGNAGE_SMOKE       1 = run smoke-test.sh after the deploy (requires APASS, BASE)
  SIGNAGE_SMOKE_ROLLBACK 0 = on smoke test failure only warn, no rollback

Before the deploy a snapshot of tv-state.json is saved to $REMOTE_ROOT/backups/.
EOF
}

DRY_RUN=0
while (($#)); do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --skip-tests) SKIP_TESTS=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown parameter: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

if [[ -z "$SSH_ALIAS" ]]; then
    echo "SIGNAGE_SSH_ALIAS is required: the name of your SSH host (an alias from ~/.ssh/config)." >&2
    echo "Example: SIGNAGE_SSH_ALIAS=my-server deploy/deploy.sh" >&2
    exit 2
fi

for command in ssh scp tar; do
    command -v "$command" >/dev/null || {
        echo "Missing command: $command" >&2
        exit 2
    }
done
test -f "$ROOT_DIR/deploy/app/Dockerfile"
git -C "$ROOT_DIR" diff --check

GIT_SHA=""
if git -C "$ROOT_DIR" rev-parse --git-dir >/dev/null 2>&1; then
    GIT_SHA="$(git -C "$ROOT_DIR" rev-parse --short=8 HEAD 2>/dev/null || true)"
    DIRTY="$(git -C "$ROOT_DIR" status --porcelain -- deploy \
        ':(exclude)deploy/app/telemetry.jsonl' 2>/dev/null || true)"
    if [[ -n "$DIRTY" ]]; then
        echo "WARNING: deploy/ has a dirty working tree (the working tree is deployed, not the commit):" >&2
        printf '%s\n' "$DIRTY" | head -20 >&2
        if [[ "$REQUIRE_CLEAN" == 1 ]]; then
            echo "SIGNAGE_REQUIRE_CLEAN=1 -> aborting" >&2
            exit 2
        fi
        [[ -z "$GIT_SHA" ]] || GIT_SHA="$GIT_SHA-dirty"
    fi
fi
# The timestamp goes first: the player compares the build as a string (newer = greater).
RELEASE="${SIGNAGE_BUILD:-$(date +%Y%m%d-%H%M%S)${GIT_SHA:+-g$GIT_SHA}}"
if [[ ! "$RELEASE" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
    echo "Invalid SIGNAGE_BUILD (allowed A-Za-z0-9_.-): $RELEASE" >&2
    exit 2
fi

if [[ "$SMOKE" == 1 && -z "${APASS:-}" ]]; then
    echo "SIGNAGE_SMOKE=1 requires APASS in the env (admin password)" >&2
    exit 2
fi

if [[ "$SKIP_TESTS" != 1 ]]; then
    # deploy/app only (nothing else)
    (cd "$ROOT_DIR/deploy/app" && pytest -q)
    if command -v ruff >/dev/null; then
        (cd "$ROOT_DIR/deploy/app" && ruff check .)
    else
        echo "WARNING: ruff is not available, lint skipped" >&2
    fi
fi

if ((DRY_RUN)); then
    printf 'dry-run: ssh=%s remote=%s container=%s release=%s\n' \
        "$SSH_ALIAS" "$REMOTE_ROOT" "$CONTAINER" "$RELEASE"
    exit 0
fi

WORK_DIR="$(mktemp -d)"
chmod 700 "$WORK_DIR"
cleanup() { rm -rf "$WORK_DIR"; }
trap cleanup EXIT
ARCHIVE="$WORK_DIR/signage-app.tgz"

tar --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='telemetry.jsonl' --exclude='*.orig' --exclude='*.rej' \
    --exclude='.pytest_cache' --exclude='.ruff_cache' --exclude='./tv-media' \
    -czf "$ARCHIVE" -C "$ROOT_DIR/deploy/app" .
scp "$ARCHIVE" "$SSH_ALIAS:$REMOTE_ROOT/signage-app.tgz"


remote_script() {
    cat <<'REMOTE'
set -eu
umask 077

REMOTE_ROOT=$1
CONTAINER=$2
HEALTH_URL=$3
AUTH_URL=$4
PLAYLIST_URL=$5
CANARY_PORT=$6
RELEASE=$7
MODE=$8
KEEP=$9
PIDS_LIMIT=${10}
CAP_DROP=${11}
ENV_FILE=""
CANARY_ENV=""
ROLLBACK_IMAGE="signage:rollback-$RELEASE"
NEW_IMAGE="signage:release-$RELEASE"
CANARY="${CONTAINER}-canary-${RELEASE}"
PREV="${CONTAINER}-prev-${RELEASE}"
STAGE="$REMOTE_ROOT/releases/$RELEASE"
STATE="$REMOTE_ROOT/data/tv-state.json"

# Container hardening that cannot break volume writes or ffmpeg.
# --cap-drop ALL is opt-in (SIGNAGE_CAP_DROP=1): root without CAP_DAC_OVERRIDE/CHOWN
# cannot write to files of another uid, which cannot be verified from here.
HARDEN="--security-opt no-new-privileges --pids-limit $PIDS_LIMIT"
HARDEN="$HARDEN --log-driver json-file --log-opt max-size=10m --log-opt max-file=3"
if [ "$CAP_DROP" = 1 ]; then
    HARDEN="$HARDEN --cap-drop ALL"
fi

cleanup() {
    docker rm -f "$CANARY" >/dev/null 2>&1 || true
    rm -f "$REMOTE_ROOT/signage-app.tgz"
    [ -z "$ENV_FILE" ] || rm -f "$ENV_FILE" "$ENV_FILE.new"
    [ -z "$CANARY_ENV" ] || rm -f "$CANARY_ENV"
}
trap cleanup EXIT
trap 'cleanup; exit 1' INT TERM HUP

test -n "$REMOTE_ROOT" -a -n "$CONTAINER" -a -n "$RELEASE"
ENV_FILE="$(mktemp /tmp/signage-env.XXXXXX)"
chmod 600 "$ENV_FILE"

health_ok() {
    attempt=0
    while [ "$attempt" -lt 15 ]; do
        if curl --fail --silent --max-time 5 "$HEALTH_URL" \
                | grep -Eq '"ok"[[:space:]]*:[[:space:]]*true'; then
            return 0
        fi
        attempt=$((attempt + 1))
        sleep 2
    done
    return 1
}

run_from_image() {
    # $1 = image, $2 = SIGNAGE_BUILD value (may be empty)
    if [ -n "$2" ]; then
        # shellcheck disable=SC2086
        docker run -d --name "$CONTAINER" --restart unless-stopped $HARDEN \
            -p 172.17.0.1:8090:8090 --env-file "$ENV_FILE" \
            -e SIGNAGE_BUILD="$2" -v "$REMOTE_ROOT/data:/data" "$1" >/dev/null
    else
        # shellcheck disable=SC2086
        docker run -d --name "$CONTAINER" --restart unless-stopped $HARDEN \
            -p 172.17.0.1:8090:8090 --env-file "$ENV_FILE" \
            -v "$REMOTE_ROOT/data:/data" "$1" >/dev/null
    fi
}

# Returns 0 only if a healthy previous version is running after the rollback.
rollback() {
    echo "Rolling back to $ROLLBACK_IMAGE" >&2
    # The rollback image must exist BEFORE anything is removed.
    if ! docker image inspect "$ROLLBACK_IMAGE" >/dev/null 2>&1; then
        echo "Rollback image $ROLLBACK_IMAGE does not exist" >&2
        return 1
    fi
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    restored=0
    if docker inspect "$PREV" >/dev/null 2>&1; then
        if docker update --restart unless-stopped "$PREV" >/dev/null \
                && docker rename "$PREV" "$CONTAINER" \
                && docker start "$CONTAINER" >/dev/null; then
            restored=1
        else
            docker rm -f "$CONTAINER" "$PREV" >/dev/null 2>&1 || true
        fi
    fi
    if [ "$restored" != 1 ]; then
        run_from_image "$ROLLBACK_IMAGE" "" || return 1
    fi
    health_ok
}

do_rollback() {
    if rollback; then
        echo "Rollback OK, previous version is running ($ROLLBACK_IMAGE)" >&2
    else
        echo "ERROR: ROLLBACK FAILED - production may be down!" >&2
        echo "  Check: docker ps -a; docker logs $CONTAINER" >&2
        echo "  Manually: docker run from image $ROLLBACK_IMAGE, data see $REMOTE_ROOT/backups/tv-state.json.pre-$RELEASE" >&2
    fi
}

if [ "$MODE" = rollback ]; then
    # Additional rollback (e.g. after a failed post-deploy smoke test).
    docker inspect "$CONTAINER" >/dev/null
    if ! docker image inspect "$ROLLBACK_IMAGE" >/dev/null 2>&1; then
        echo "ERROR: rollback image $ROLLBACK_IMAGE does not exist, nothing was removed" >&2
        exit 1
    fi
    docker inspect --format='{{range .Config.Env}}{{println .}}{{end}}' \
        "$CONTAINER" | grep -Ev '^SIGNAGE_BUILD=' > "$ENV_FILE" || true
    PREV_BUILD=""
    if [ -f "$STAGE/.prev-build" ]; then
        PREV_BUILD="$(cat "$STAGE/.prev-build")"
    fi
    docker rm -f "$CONTAINER" >/dev/null
    if run_from_image "$ROLLBACK_IMAGE" "$PREV_BUILD" && health_ok; then
        echo "Rollback OK, previous version is running ($ROLLBACK_IMAGE)"
        exit 0
    fi
    echo "ERROR: ROLLBACK FAILED - production may be down!" >&2
    exit 1
fi

check_instance() {
    health_url=$1
    auth_url=$2
    playlist_url=$3
    expected=$4
    body=""
    attempt=0
    while [ "$attempt" -lt 30 ]; do
        if body="$(curl --fail --silent --show-error --max-time 5 "$health_url")" \
                && printf '%s' "$body" | grep -Eq '"ok"[[:space:]]*:[[:space:]]*true' \
                && printf '%s' "$body" | grep -Eq '"version"[[:space:]]*:[[:space:]]*"'"$expected"'"'; then
            break
        fi
        attempt=$((attempt + 1))
        sleep 1
    done
    [ "$attempt" -lt 30 ] || return 1
    code="$(curl --silent --show-error --max-time 15 -o /dev/null \
        -w '%{http_code}' "$auth_url")" || return 1
    [ "$code" = 401 ] || return 1
    curl --fail --silent --show-error --max-time 15 "$playlist_url" \
        | grep -Eq '"items"[[:space:]]*:' || return 1
}

prune_old() {
    # Retention: keep the $KEEP newest images, release directories and state snapshots.
    # The current release is never deleted.
    for pfx in release rollback; do
        docker images --format '{{.Tag}}' signage | grep "^$pfx-" | sort -r \
            | tail -n +$((KEEP + 1)) | while IFS= read -r t; do
                [ "$t" = "$pfx-$RELEASE" ] && continue
                docker rmi "signage:$t" >/dev/null 2>&1 || true
            done
    done
    find "$REMOTE_ROOT/releases" -mindepth 1 -maxdepth 1 -type d | sort -r \
        | tail -n +$((KEEP + 1)) | while IFS= read -r d; do
            [ "$d" = "$STAGE" ] && continue
            rm -rf -- "$d"
        done
    if [ -d "$REMOTE_ROOT/backups" ]; then
        find "$REMOTE_ROOT/backups" -mindepth 1 -maxdepth 1 -type f \
            -name 'tv-state.json.pre-*' | sort -r \
            | tail -n +$((KEEP + 1)) | while IFS= read -r f; do
                rm -f -- "$f"
            done
    fi
}

test -f "$REMOTE_ROOT/signage-app.tgz"
if [ -e "$STAGE" ]; then
    echo "Release already exists: $STAGE" >&2
    exit 2
fi
docker inspect "$CONTAINER" >/dev/null
docker inspect --format='{{range .Config.Env}}{{println .}}{{end}}' \
    "$CONTAINER" > "$ENV_FILE"
# Behind nginx: per-IP limits need X-Forwarded-For, otherwise all clients
# look like the proxy IP and 10 bad logins block everybody.
grep -q '^SIGNAGE_TRUST_PROXY=' "$ENV_FILE" || echo 'SIGNAGE_TRUST_PROXY=1' >> "$ENV_FILE"
# SIGNAGE_BUILD is passed via -e; remove it from the env file so that lines
# do not pile up with every deploy. Previous value = last line.
PREV_BUILD_VALUE="$(sed -n 's/^SIGNAGE_BUILD=//p' "$ENV_FILE" | tail -n 1)"
grep -Ev '^SIGNAGE_BUILD=' "$ENV_FILE" > "$ENV_FILE.new" || true
mv "$ENV_FILE.new" "$ENV_FILE"
CANARY_ENV="$(mktemp /tmp/signage-canary-env.XXXXXX)"
chmod 600 "$CANARY_ENV"
grep -Ev '^SIGNAGE_TG_(TOKEN|CHAT)=' "$ENV_FILE" > "$CANARY_ENV"

mkdir -p "$STAGE"
printf '%s' "$PREV_BUILD_VALUE" > "$STAGE/.prev-build"
tar -xzf "$REMOTE_ROOT/signage-app.tgz" -C "$STAGE"
docker build -t "$NEW_IMAGE" "$STAGE"

docker rm -f "$CANARY" >/dev/null 2>&1 || true
# shellcheck disable=SC2086
docker run -d --name "$CANARY" --restart=no $HARDEN \
    -p "127.0.0.1:$CANARY_PORT:8090" \
    --env-file "$CANARY_ENV" -e SIGNAGE_BUILD="$RELEASE" \
    -v "$REMOTE_ROOT/data:/data:ro" "$NEW_IMAGE" >/dev/null
if ! check_instance \
        "http://127.0.0.1:$CANARY_PORT/api/health" \
        "http://127.0.0.1:$CANARY_PORT/admin" \
        "http://127.0.0.1:$CANARY_PORT/api/tv/default/playlist" \
        "$RELEASE"; then
    echo "Canary health/auth/playlist check failed" >&2
    exit 1
fi
docker rm -f "$CANARY" >/dev/null

# The rollback image must be verified before the running container is stopped.
OLD_IMAGE_ID="$(docker inspect --format='{{.Image}}' "$CONTAINER")"
docker tag "$OLD_IMAGE_ID" "$ROLLBACK_IMAGE"
if [ "$(docker image inspect --format='{{.Id}}' "$ROLLBACK_IMAGE" 2>/dev/null)" != "$OLD_IMAGE_ID" ]; then
    echo "ERROR: could not verify rollback image $ROLLBACK_IMAGE, leaving production unchanged" >&2
    exit 1
fi

# State snapshot before the switch (a rollback restores only the code, not data/schema).
if [ -f "$STATE" ]; then
    mkdir -p "$REMOTE_ROOT/backups"
    cp -p "$STATE" "$REMOTE_ROOT/backups/tv-state.json.pre-$RELEASE"
    echo "State snapshot: $REMOTE_ROOT/backups/tv-state.json.pre-$RELEASE"
else
    echo "WARNING: $STATE does not exist, state snapshot skipped" >&2
fi

# The old container is only stopped and renamed (not deleted) - a rollback can bring it back.
if ! docker stop "$CONTAINER" >/dev/null; then
    echo "ERROR: could not stop $CONTAINER, nothing changed" >&2
    exit 1
fi
if ! { docker update --restart=no "$CONTAINER" >/dev/null \
        && docker rename "$CONTAINER" "$PREV"; }; then
    docker update --restart unless-stopped "$CONTAINER" >/dev/null 2>&1 || true
    docker start "$CONTAINER" >/dev/null 2>&1 || true
    echo "ERROR: renaming the old container failed, starting the original" >&2
    exit 1
fi
# shellcheck disable=SC2086
if ! docker run -d --name "$CONTAINER" --restart unless-stopped $HARDEN \
        -p 172.17.0.1:8090:8090 --env-file "$ENV_FILE" \
        -e SIGNAGE_BUILD="$RELEASE" -v "$REMOTE_ROOT/data:/data" \
        "$NEW_IMAGE" >/dev/null; then
    do_rollback
    exit 1
fi

if ! check_instance "$HEALTH_URL" "$AUTH_URL" "$PLAYLIST_URL" "$RELEASE"; then
    echo "Production health/auth/playlist check failed" >&2
    do_rollback
    exit 1
fi

docker rm -f "$PREV" >/dev/null 2>&1 || true
prune_old
echo "Deploy $RELEASE OK; rollback image: $ROLLBACK_IMAGE"
REMOTE
}

remote_run() {
    remote_script | ssh "$SSH_ALIAS" sh -s -- "$REMOTE_ROOT" "$CONTAINER" \
        "$HEALTH_URL" "$AUTH_URL" "$PLAYLIST_URL" "$CANARY_PORT" "$RELEASE" \
        "$1" "$KEEP_RELEASES" "$PIDS_LIMIT" "$CAP_DROP"
}

remote_run deploy

if [[ "$SMOKE" == 1 ]]; then
    echo "Post-deploy smoke test..."
    if ! "$ROOT_DIR/deploy/smoke-test.sh"; then
        if [[ "$SMOKE_ROLLBACK" == 1 ]]; then
            echo "Smoke test failed, rolling back" >&2
            if remote_run rollback; then
                echo "Rollback after the smoke test failure done" >&2
            else
                echo "ERROR: rollback after the smoke test FAILED, check production manually" >&2
            fi
        else
            echo "WARNING: smoke test failed (SIGNAGE_SMOKE_ROLLBACK=0, no rollback)" >&2
        fi
        exit 1
    fi
fi
