# Installation

[Česky](../cs/install.md)

Placeholders: `<host>` (public address of the service), `<ssh-host>` (an alias
from your `~/.ssh/config`), `<screen>` (screen name, e.g. `lobby-1a2b3c4d5e6f`),
`<user>` and `<password>` (admin login). Keep real values in a private,
untracked file, never in git.

## Docker Compose

Requires Docker with the Compose plugin.

```bash
git clone https://github.com/PeterkoCZ91/plain-signage signage && cd signage
cp .env.example .env            # then edit .env: set SIGNAGE_ADMIN_USER and SIGNAGE_ADMIN_PASS
docker compose up -d --build
curl -s http://localhost:8080/api/health
```

Open `http://localhost:8080/admin` (basic auth with the credentials from `.env`),
create a screen with **+ New screen** and open `http://localhost:8080/tv/<screen>`.
`docker-compose.yml` hands the container these variables from `.env`:
`SIGNAGE_ADMIN_USER` and `SIGNAGE_ADMIN_PASS` (both required) and the default
brand `SIGNAGE_BRAND_NAME`, `_EMAIL`, `_PHONE`, `_CITY`, `_URL`, `_QR_EMAIL`,
`_PWA_TITLE` (placeholder defaults). `SIGNAGE_PORT` (host port, default 8080) is
used by Compose only. Through `env_file: .env` every other variable you put in
`.env` also reaches the container, so `SIGNAGE_TG_TOKEN`, `SIGNAGE_PUBLIC_URL`,
`SIGNAGE_MAX_FILE_MB` and the rest of [Configuration](#configuration) work
without editing the compose file; the explicit entries win over `env_file`. If
port 8080 is taken, set `SIGNAGE_PORT=<port>` in `.env` and use that port in all
URLs. Data live in the
`signage-data` volume at `/data`. `docker compose down` keeps the volume; add
`-v` to delete it.

## Without Docker

Requirements:

- `python3`. CI and the image use 3.12; older versions are untested. Optional:
  `segno` (QR codes; without it the screen and pairing QR endpoints return 503
  and `/qr.png` serves the built-in `default-qr.png`) and `ffmpeg` (thumbnails,
  repackaging, conversion; without it `/api/health` reports
  `"autoconvert": false`).
- For the tests: `pytest` (`pip install pytest`) and Node (CI uses 20).
- For `backup.sh`, `restore.sh` and `test-backup-restore.sh`: `bash`, `flock`,
  `tar`, `df`.
- `PYTHONUTF8=1` makes Python read non-ASCII admin passwords correctly
  regardless of the system locale.

Keep everything in one data directory (`tv-state.json` plus `tv-media/`), which is
the layout `backup.sh` and `restore.sh` expect. Run from the repository root:

```bash
SIGNAGE_ADMIN_USER=<user> SIGNAGE_ADMIN_PASS=<password> \
SIGNAGE_MEDIA=./data/tv-media SIGNAGE_STATE=./data/tv-state.json \
PYTHONUTF8=1 python3 deploy/app/tvserver.py 8090
```

The port is an optional first argument (default 8090; pass another number to
change it). The server listens on all interfaces (`SIGNAGE_BIND` changes that)
and its start-up message prints the admin and TV URLs with the machine's LAN
address. Without both admin variables the server refuses to start. Quick checks:

```bash
curl -s http://localhost:8090/api/health                                   # {"ok": true, ...}
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8090/admin       # 401
curl -s -o /dev/null -w '%{http_code}\n' -u '<user>:<password>' http://localhost:8090/admin   # 200
```

Backup and restore without Docker (restore details:
[troubleshooting.md](troubleshooting.md#restore-from-a-data-backup)):

```bash
SRC=./data DEST=./backups ./deploy/backup.sh
```

Tests (the full list of checks, including ruff, shellcheck, the kiosk Java
tests and E2E, is in the README). Run all of them from the repository root, each in its own command. The
subshell keeps `pytest` from changing your directory, and `node --test` finds no
files (and reports 0 tests) when started from `deploy/app`; 0 tests is not a
pass. The E2E tests need `playwright` with Chromium and skip themselves without
it; the `node --test` glob matters, the directory form fails on Node 22:

```bash
(cd deploy/app && python3 -m pytest -q)
node --test deploy/app/tv/test/*.test.mjs
bash deploy/test-backup-restore.sh
```

Docker without Compose:

```bash
docker build -t signage:latest deploy/app
mkdir -p ./signage-data
docker run -d --name signage --restart unless-stopped \
  -p 127.0.0.1:8090:8090 \
  -e SIGNAGE_ADMIN_USER=<user> -e SIGNAGE_ADMIN_PASS=<password> \
  -e PYTHONUTF8=1 \
  -v "$PWD/signage-data:/data" \
  --log-opt max-size=10m --log-opt max-file=3 \
  signage:latest
```

The image is `python:3.12-slim` plus `segno` and `ffmpeg`. The `Dockerfile` copies
every server module explicitly, so a new module must be added there. The
container runs as root (volume ownership).

## Configuration

All optional except the admin credentials. The full list is in
[api.md](api.md#environment-variables); the main ones:

| Variable | Purpose (default) |
|---|---|
| `SIGNAGE_ADMIN_USER`, `SIGNAGE_ADMIN_PASS` | Admin credentials (required) |
| `SIGNAGE_MEDIA`, `SIGNAGE_STATE` | Media directory, state file |
| `SIGNAGE_PUBLIC_URL` | Public base URL for QR codes; otherwise taken from request headers |
| `SIGNAGE_BUILD` | Version string in health and the playlist (`dev`); players reload when it changes |
| `SIGNAGE_TRUST_PROXY` | `1` behind a reverse proxy: client IP from `X-Forwarded-For` |
| `SIGNAGE_MAX_FILE_MB`, `SIGNAGE_MAX_ITEMS`, `SIGNAGE_MAX_TOTAL_MB` | Upload limits (100 / 50 / 2000) |
| `SIGNAGE_TG_TOKEN`, `SIGNAGE_TG_CHAT`, `SIGNAGE_ALERT_OFFLINE_MIN`, `SIGNAGE_ALERT_LANG` | Telegram alerts (off; 10 min; `en`, or `cs` for Czech) |
| `SIGNAGE_KIOSK_DIR` | Kiosk release directory (`kiosk/` next to the state file) |
| `SIGNAGE_BRAND_*` | Default brand, overridable per screen in the admin |
| `SIGNAGE_BRAND_WORDMARK` (`LEAD\|REST`), `_TAGLINE_CS`, `_TAGLINE_EN`, `_EXTRA_QR_URL`, `_EXTRA_QR_LABEL_CS`, `_EXTRA_QR_LABEL_EN` | Idle-screen wordmark and tagline, optional third QR shortcut |
| `SIGNAGE_BRAND_FILE` (`brand.env` next to the state file), `SIGNAGE_ASSETS_DIR` (`assets/` next to the state file) | The same `SIGNAGE_BRAND_*` as a `KEY=VALUE` file; files in `assets/` (`default-qr.png`, icons, `manifest.webmanifest`) replace the neutral built-in ones |

The built-in brand, icons and QR image are neutral demo values: set your own.

## Production deployment

`deploy/deploy.sh` deploys to a Docker host over SSH, behind a reverse proxy
that terminates TLS. Details: `./deploy/deploy.sh --help` and `deploy/README.md`.

Prepare the host once:

1. Create the directories:

   ```bash
   ssh <ssh-host> 'mkdir -p /opt/signage/data /opt/signage/backups /opt/signage/releases'
   ```

2. Add an SSH alias to your local `~/.ssh/config` and run the script with
   `SIGNAGE_SSH_ALIAS=<ssh-host>` (required). The other `deploy.sh` variables
   (`./deploy/deploy.sh --help` prints them too):

   | Variable | Meaning (default) |
   |---|---|
   | `SIGNAGE_REMOTE_ROOT` | Remote root (`/opt/signage`) |
   | `SIGNAGE_CONTAINER` | Container name (`signage`) |
   | `SIGNAGE_HEALTH_URL`, `SIGNAGE_AUTH_URL`, `SIGNAGE_PLAYLIST_URL` | Internal URLs checked on the host (`http://172.17.0.1:8090/...`; the playlist check uses the screen `default`) |
   | `SIGNAGE_CANARY_PORT` | Port of the canary container (18090) |
   | `SIGNAGE_BUILD` | Build version (timestamp-gitSHA[-dirty]) |
   | `SIGNAGE_KEEP_RELEASES` | Images and release directories to keep (5) |
   | `SIGNAGE_PIDS_LIMIT` | `--pids-limit` of the container (256) |
   | `SIGNAGE_CAP_DROP` | `1` adds `--cap-drop ALL` (0; verify on your host) |
   | `SIGNAGE_REQUIRE_CLEAN` | `1` fails on a dirty `deploy/` working tree (0) |
   | `SIGNAGE_SKIP_TESTS` | `1` = `--skip-tests` (0) |
   | `SIGNAGE_SMOKE`, `SIGNAGE_SMOKE_ROLLBACK` | `1` runs `smoke-test.sh` after the deploy (needs `BASE`, `APASS`); `0` for the rollback only warns instead of rolling back (1) |
3. Create the container `signage` by hand once. The script takes its environment
   from it (secrets never go on the command line) and stops if it does not exist.

   ```bash
   ssh <ssh-host> 'mkdir -p /opt/signage/app'
   scp -r deploy/app/. <ssh-host>:/opt/signage/app/
   ssh <ssh-host> 'cd /opt/signage/app && docker build -t signage:latest .'
   ssh <ssh-host> 'docker run -d --name signage --restart unless-stopped \
     -p 172.17.0.1:8090:8090 \
     -e SIGNAGE_ADMIN_USER=<user> -e SIGNAGE_ADMIN_PASS=<password> \
     -e PYTHONUTF8=1 -e SIGNAGE_TRUST_PROXY=1 \
     -v /opt/signage/data:/data \
     --log-opt max-size=10m --log-opt max-file=3 signage:latest'
   ```

   The port is bound to the Docker bridge address, not a public interface.
4. Put a reverse proxy (nginx) in front, sending `X-Forwarded-For`, with
   `SIGNAGE_TRUST_PROXY=1` in the container. Without it every client shares the
   proxy's IP and ten mistyped passwords lock everybody out; with it and no
   proxy a client can forge its IP.

   ```nginx
   server {
       listen 443 ssl;
       server_name <host>;
       # ssl_certificate / ssl_certificate_key
       client_max_body_size 110m;              # above SIGNAGE_MAX_FILE_MB
       location / {
           proxy_pass http://172.17.0.1:8090;
           proxy_set_header Host $host;
           proxy_set_header X-Forwarded-For $remote_addr;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   ```

   Old panels that cannot do HTTPS need a plain HTTP server on port 80 with the
   same `proxy_pass`. A new `limit_req_zone` is not picked up by
   `nginx -s reload`; restart nginx.

Deploy:

```bash
./deploy/deploy.sh --dry-run     # tests and configuration check, uploads nothing
./deploy/deploy.sh               # tests, package, canary, switch, health/auth/playlist checks, rollback on failure
./deploy/deploy.sh --skip-tests  # emergency only
```

The script runs the tests, builds the image on the host and starts a canary
container (no Telegram credentials, data read-only). It then snapshots
`tv-state.json` to `/opt/signage/backups/tv-state.json.pre-<release>`, swaps the
containers and checks again; on any failure it rolls back. Players pick up the
new version within about two minutes. Optional smoke test of the live service:

```bash
BASE=https://<host> AUSER='<user>' APASS='<password>' ./deploy/smoke-test.sh
```

Always pass `BASE` and `APASS` (`AUSER` defaults to `admin`). One check expects HSTS, `X-Frame-Options` and
`X-Content-Type-Options` headers, which the proxy must add.

## First login and players

Open `https://<host>/admin`. A new service has no screens: click **+ New screen** and
give it a label (the screen gets a name like `<slug>-<12 hex>`), or use the API:

```bash
curl -s -u '<user>:<password>' -H 'Content-Type: application/json' \
  -d '{"label":"Lobby"}' https://<host>/api/screens
# {"ok": true, "device": "lobby-<12 hex>", "label": "Lobby"}
```

`<screen>` in the URLs is the screen's name from the admin. The name `default` works but is guessable, and media are readable by anyone who
knows a screen's URL, so do not use it in production. Changing the admin
password means recreating the container with a new `-e SIGNAGE_ADMIN_PASS=...`;
a normal deploy then keeps it.

Open `https://<host>/tv/<screen>` on a TV or in a browser. The player keeps media
in Cache Storage and plays on after the network drops once the playlist has been
downloaded completely; the first load without internet does not work.

- A heartbeat is sent only for a screen that exists, so create it first.
- Sound and full screen in a normal browser wait for a click or key press.
- Language: `?lang=cs|en`, otherwise the browser language.
- Item schedules use the device clock: check date and time zone on the box or TV.
- Video must be H.264 in MP4: `./deploy/convert-video.sh INPUT [OUTPUT]`.

## Backups

`deploy/backup.sh` archives `/opt/signage/data` to
`/opt/signage/backups/signage-data-<STAMP>.tgz` (lock, free-space check,
`tar -tzf` verification, keeps the last 14). Backups stay on the same host unless
you copy them elsewhere. Restore: [troubleshooting.md](troubleshooting.md#restore-from-a-data-backup).

```bash
scp deploy/backup.sh <ssh-host>:/opt/signage/backup.sh
ssh <ssh-host> 'chmod +x /opt/signage/backup.sh &&
  (crontab -l 2>/dev/null | grep -v signage/backup.sh;
   echo "10 3 * * * /opt/signage/backup.sh >> /opt/signage/backup.log 2>&1") | crontab -'
ssh <ssh-host> /opt/signage/backup.sh        # run one now
```

## Verification

```bash
BASE=https://<host>
curl -s "$BASE/api/health"                                            # ok=true, state_ok=true
curl -s -o /dev/null -w '%{http_code}\n' "$BASE/admin"                # 401
curl -s -o /dev/null -w '%{http_code}\n' -u '<user>:<password>' "$BASE/admin"   # 200
curl -s "$BASE/api/tv/<screen>/playlist"                              # contains "items"
curl -s -o /dev/null "$BASE/api/tv/<screen>/playlist?heartbeat=1&ok=1&err=0"
curl -s -u '<user>:<password>' "$BASE/api/devices"                    # "online": true within 45 s
docker inspect signage --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -c '^SIGNAGE_TRUST_PROXY=1$'   # 1
```

The last line prints only a count, not secrets.
