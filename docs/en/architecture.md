# Architecture

[Česky](../cs/architecture.md)

## Components

- Server, `deploy/app/tvserver.py`: `ThreadingHTTPServer`, no web framework. Handler, state file, upload, pairing, thumbnails, telemetry, alert delivery. Helper modules (`errors.py`, `validators.py`, `brand.py`, `ratelimit.py`, `mediacheck.py`, `mediautil.py`, `statefmt.py`, `alerts.py`) hold pure functions; `multipart.py` streams uploads to disk; `video_convert.py` and `fix_media.py` repackage video with ffmpeg. The only optional dependencies are `segno` (QR) and `ffmpeg`.
- Player, admin, pairing page and service worker in `deploy/app/tv/`: `player.html`, `admin.html` (Czech and English), `connect.html`, `sw.js`.
- Kiosk app (`kiosk-app/`) and Wi-Fi helper (`wifi-app/`): see [kiosk.md](kiosk.md).
- Deploy scripts in `deploy/`: `deploy.sh`, `backup.sh`, `restore.sh`, `smoke-test.sh`, `convert-video.sh`, `tools/publish-kiosk.sh`, `tools/soak.py`.
- Languages: admin and pairing page have Czech and English dictionaries (`?lang=`, then `localStorage` `signageLang`, then the browser, default Czech). The kiosk uses `res/values` (English) and `res/values-cs`. Server errors come from one table (`ERRORS`) with a stable `code`; without a language request a terse English text is returned.
- Tests and CI: server tests (`deploy/app/test_*.py`, unit and HTTP integration), client tests (`node --test`, 72 tests of player, service worker, i18n and schedule in `node:vm`), Chromium E2E with Playwright (`deploy/app/e2e/`, part of `pytest`), plain-Java kiosk tests in `kiosk-app/test/` and the backup/restore round trip. CI (`.github/workflows/ci.yml`) runs ruff, pytest, shellcheck, the backup/restore test, `node --test`, the Chromium E2E and a `docker compose` smoke test (the admin must answer 401). Commands: [install.md](install.md#without-docker).

```
admin, smart TV, Android kiosk ──HTTPS──┐   reverse proxy (nginx)
old panel ───────────────────────HTTP──┤        │
                                        ▼
                        container: tvserver.py (port 8090)
                        /admin  /tv/<screen>  /connect  /api/...
                        volume /data: media, tv-state.json, telemetry, kiosk release
```

## State and screens

A screen is a record under `devices` in the state file (`SIGNAGE_STATE`, JSON with
a `schema_version`) with `version`, `items` and `settings`. Items have `id`,
`file`, `type`, `seconds`, `enabled`, `from`/`until`, an optional `schedule` and
`sound`; settings hold transition, fit mode, QR options, `brand` and `label`.
The screen name is part of the URL and matches `^[A-Za-z0-9_-]{1,48}$`. New
screens get `<slug>-<12 hex>`, the random part being 48 bits from `secrets`.
Every change increments `version`, which the player sees in the playlist.

State is written atomically (temporary file, `fsync`, `os.replace`) under a lock.
A corrupt file is copied aside and writes are blocked, so `/api/health` returns
`ok: false`. Presence, upload reservations, QR cache, rate limiters, idempotent
replies and box health live in memory only. Item schedules are evaluated by the
player on the device clock, not by the server ([api.md](api.md#item-schedule)).

## Data flow, pairing and monitoring

- Upload: multipart, type and size validation, quota reservation, streaming into `.signage-upload-*`, optional ffmpeg repackage, rename to `<16 hex>_<name>`, save.
- The player polls the playlist every 15 s with `?heartbeat=1`; a new `SIGNAGE_BUILD` makes it reload on a transition between items, and an older build is never treated as newer.
- Pairing: a TV opens `/connect` and gets a six-character code valid for ten minutes; the QR code leads to the admin with the code prefilled. After the admin claims it, the TV moves to `/tv/<screen>` on its next poll and remembers it.
- Monitoring: only the player sends heartbeats, an open admin does not count. A screen is online if heard from within 45 s. Telemetry goes to `SIGNAGE_TELEMETRY` at most once a minute (kept 14 days, capped at 8 MiB); the admin shows the last 24 hours in hourly blocks. This proves the player runs, not the state of the panel. An alert loop (60 s) sends Telegram messages for offline, blank and low-RAM screens.
- Offline: the service worker caches the shell, whole media files (with `Range` support) and the last complete playlist, and plays it when the network drops. The first load and an unfinished download need internet. Workbox is bundled; nothing comes from a CDN.
- Kiosk update and box health ride on one poll, `GET /api/kiosk/release`: [api.md](api.md#kiosk-update).

## Security model

- Public by design: player, playlist, `/media/`, `/api/health`, pairing, `/qr.png`, the kiosk poll and APK. A screen's address is effectively the password for its content, hence unguessable names and random upload prefixes.
- Basic auth (constant-time comparison, 401 closes the connection) for the admin and the write API.
- Per-IP rate limits with `Retry-After`; set `SIGNAGE_TRUST_PROXY=1` only behind a proxy.
- Limits: JSON body 64 KiB, form text field 1 MB, upload size and total storage caps (413, 507), at most 3 concurrent uploads (503), socket timeout. `brand` and `label` accept no `<` or `>`; `media_path` never follows a symlink out of `SIGNAGE_MEDIA`.
- `admin.html` carries a Content-Security-Policy as a `<meta>` tag (`default-src 'self'`; inline scripts and styles are allowed, `eval` is not). It is not an HTTP header and it does not apply to the player or `/connect`. Admin pages also send `X-Robots-Tag: noindex`.
- Kiosk: cleartext traffic and backups disabled, navigation restricted to the configured https host.
- The container runs with `no-new-privileges`, a pids limit and log rotation.
- Not independently reviewed.

## Known limitations

- One admin password, no roles, no multi-tenant separation; screen names are the only separation between screens.
- The kiosk has no PIN: pressing BACK twice leaves it and pauses the watchdog for 10 minutes (deliberate, for technicians). On Android 10 the watchdog needs `SYSTEM_ALERT_WINDOW` granted once over ADB. Kiosk `targetSdk` is 29.
- The container runs as root; backups stay on the same host unless you copy them.
- Not automated: real TVs and panels, the kiosk on a real box (autostart after a power cut), deployment (checked by hand and by the smoke test). The Java kiosk tests are not run in CI. Load is measured manually with `deploy/tools/soak.py`.
- Language defaults: English. API errors without a language request, the TV player and Telegram alerts are English; Czech is available with `?lang=cs` / `Accept-Language` for API errors, the player language and `SIGNAGE_ALERT_LANG=cs` for alerts. Alerts use text prefixes (`[CRITICAL]`, `[WARNING]`, `[OK]`), not emoji.
