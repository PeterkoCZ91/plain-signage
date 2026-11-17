# HTTP API

[Česky](../cs/api.md)

Source of truth: `deploy/app/tvserver.py` (routes), `errors.py`, `validators.py`,
`brand.py`, `ratelimit.py` and the test `test_api_matrix.py`. Placeholders:
`<host>`, `<user>:<password>` (admin login), `<screen>` (screen name). Only GET,
HEAD and POST are implemented; PUT, DELETE, PATCH and OPTIONS return `501`.

## Authentication

- Protected paths use HTTP Basic with `SIGNAGE_ADMIN_USER` and `SIGNAGE_ADMIN_PASS`.
- Protected by prefix: `/admin`, `/api/upload`, `/api/delete`, `/api/order`,
  `/api/item`, `/api/devices`, `/api/settings`, `/api/pair/claim`, `/api/screens`,
  `/api/thumb/`, and exactly `/`.
- Public: `/tv`, `/tv/<screen>`, `/connect`, `/api/health`,
  `/api/tv/<screen>/playlist`, `/media/<file>`, `/assets/<file>`, `/sw.js`,
  `/robots.txt`, `/qr.png`, `/pair-qr.png`, `POST /api/pair/request`,
  `GET /api/pair/status`, `GET /api/kiosk/release`, `/kiosk/signage-kiosk.apk`.
  The public player is safe only because screen names are random (`<slug>-<12 hex>`).
- Bad or missing credentials: `401` with `WWW-Authenticate: Basic realm="Signage admin"`,
  code `unauthorized`, connection closed. Too many failures from one IP: `429`
  `too_many_attempts`; only requests with an `Authorization` header count.

## Errors

JSON errors (except upload) look like `{"error": "invalid device", "code": "invalid_device"}`.

- `code` is stable and machine-readable (a key of `ERRORS` in `errors.py`); branch on it.
- `error` is human text. Without a language request it is a terse technical English
  text (ASCII only). `?lang=cs|en` or `Accept-Language` gives Czech or English
  text meant for end users; `?lang=` wins.
  Replies carry `Content-Language`. User data is not translated.
- Errors without a JSON body (unknown GET path, missing media, QR errors) are plain text.

| Code | HTTP | When |
|---|---|---|
| `unauthorized`, `too_many_attempts` | 401, 429 | missing or wrong credentials, too many failed logins |
| `too_many_requests` | 429 | limit of `POST /api/pair/request` or `GET /api/kiosk/release` |
| `pair_capacity_full` | 429 | 200 pending pairing codes exist |
| `invalid_json`, `body_not_object`, `invalid_content_length` | 400 | malformed request body |
| `request_too_large` | 413 | JSON body over 64 KiB |
| `unknown_endpoint` | 404 | unknown POST path |
| `invalid_device` | 400 / 404 | invalid screen name (404 for a playlist) |
| `preview_unavailable` | 404 | thumbnail missing or cannot be created |
| `invalid_install_id`, `invalid_pair_input` | 400 | `client` not `[A-Za-z0-9_-]{16,80}`; code not `[A-Z2-9]{6}` or bad name |
| `pair_code_expired`, `pair_code_used` | 404, 409 | code unknown or expired, already used |
| `state_save_failed` | 500 / 507 | saving state failed (507 = disk full) |
| `no_kiosk_release`, `kiosk_up_to_date` | 404 or 409, 409 | no valid release (404 on the poll, 409 on `kiosk-update`); screen already has this `versionCode` or higher |
| `hours_not_integer`, `hours_out_of_range` | 400 | `hours` must be 1 to 168 |
| `order_not_list`, `file_not_string`, `seconds_invalid`, `schedule_invalid` | 400 | item and order input (`seconds` up to 86400) |
| `label_not_text`, `label_too_long`, `label_invalid_chars` | 400 | label: max 60 characters, no `<` `>` or control characters |
| `brand_*` | 400 | `brand_not_object`, `brand_field_not_text`, `_too_long`, `_bad_chars`, `_not_email`, `brand_url_invalid` |
| `too_many_uploads`, `invalid_idempotency_key`, `upload_in_progress`, `server_busy` | 429, 400, 409, 503 | see Upload |
| `batch_too_large`, `file_too_large`, `upload_corrupt`, `file_save_failed` | 413, 413, 400, 507 | see Upload |
| `unsupported_type`, `heic_unsupported`, `video_format_unsupported`, `item_limit`, `empty_file`, `media_invalid`, `storage_quota`, `invalid_file_path`, `media_*` | in the upload error list | `media_*` (`media_codec`, `media_quicktime`, `media_image_huge`, ...) detail a `media_invalid` |
| `warn_*`, `fix_repacked*` | `warnings` / `fixes` | not errors: texts of warnings and automatic fixes |

The full list with texts is `ERRORS` in `deploy/app/errors.py`.

## Endpoints

Protected endpoints need basic auth, e.g. `curl -u <user>:<password> https://<host>/...`.
`device` in all JSON endpoints must match `[A-Za-z0-9_-]{1,48}`.

### Public

| Method and path | Description |
|---|---|
| `GET /tv`, `/tv/<screen>` | HTML player (no name = `default`), `no-store` |
| `GET /connect` | HTML pairing page |
| `GET /api/tv/<screen>/playlist` | `{version, device, items[], build, settings}`. `heartbeat=1` records a heartbeat; also `w`, `h`, `why`, `h264`, `quota`, `kiosk` and other diagnostics. Invalid name: 404 `invalid_device`; a well-formed but unknown name gets `version` 0 and no items (nothing is created, no heartbeat recorded) |
| `GET /api/health` | `{ok, state_ok, state_error, devices, items, storage_mb, version, blank_screens, alerts_enabled, autoconvert, max_file_mb, max_items, max_total_mb}` |
| `GET /media/<file>` | media with `Range` support (206/416), `Cache-Control: public, max-age=3600` |
| `GET /assets/<file>`, `/sw.js`, `/robots.txt` | static files, service worker, `Disallow: /` |
| `GET /qr.png` | QR of a screen's setup; `device`, `slot` (`1` = web/IG, `2` = e-mail) |
| `GET /pair-qr.png` | QR to `/admin?pair=<code>`; `code` |
| `POST /api/pair/request` | TV sends `{"client": "<install id>"}`, gets `{code, expires_in}` (6 characters `A-Z2-9`, 600 s; the same `client` gets the same code) |
| `GET /api/pair/status` | `client`; `{"status":"pending","expires_in":N}`, `{"status":"paired","device":"<screen>"}` (consumes the code) or 404 `{"status":"missing"}` |
| `GET /api/kiosk/release` | poll by the kiosk app, once a minute (see Kiosk update). `device`, `have` (installed `versionCode`), `state` (`idle`, `downloading`, `waiting`, `installed`, `declined`, `failed`, `no_permission`), box health; invalid values are ignored, never 400. Reply `{versionCode, versionName, sha256, size, url, requested}`, `no-store` |
| `GET /kiosk/signage-kiosk.apk` | current APK, supports `Range`; 404 without a valid release |

### Protected

| Method and path | Description |
|---|---|
| `GET /`, `/admin` | HTML admin |
| `GET /api/devices` | `{"devices": {<screen>: {version, count, label, size_mb, online, last_seen, agent, screen, render_err, render_why, h264, storage_mb, kiosk, cached_items, snapshots, stalls, blank, kiosk_code, kiosk_state, kiosk_update, kiosk_*}}, "kiosk_release": {versionCode, versionName} or null}`; `online` = heartbeat within 45 s; `kiosk_update` = requested `versionCode` or null |
| `GET /api/devices/<screen>/availability` | availability from telemetry; `hours` 1 to 168 (default 24) |
| `GET /api/thumb/<file>` | JPEG thumbnail made lazily with ffmpeg and cached; 404 `preview_unavailable` |
| `GET /api/screens/<screen>/qr.png` | QR with the screen's player address |
| `POST /api/screens` | `{"label": "..."}` (optional); creates a screen with a random name, reply `{ok, device, label}` |
| `POST /api/screens/<screen>/kiosk-update` | `{}` requests a kiosk update (stores the current `versionCode`), `{"cancel": true}` cancels; reply `{"ok": true, "requested": <versionCode> or null}` |
| `POST /api/pair/claim` | `code` and `device` (existing or new), or no `device` for a new screen with optional `label`; reply `{ok, device}` |
| `POST /api/upload?device=<screen>` | multipart upload, see Upload |
| `POST /api/order` | `device`, `order` (list of file names; unlisted items stay at the end) |
| `POST /api/delete` | `device`, `file`; the file leaves the disk only if no other screen uses it |
| `POST /api/item` | `device`, `file`, optionally `seconds`, `sound`, `enabled`, `from`, `until`, `schedule` |
| `POST /api/settings` | `device`, optionally `qrCorner`, `qr1On`, `qr2On`, `qrUrl`, `qrUrl2`, `brand`, `label`, `transition`, `fit`; reply `{ok, settings}` |

Notes:

- `/api/item`: `seconds` below 2 is raised to 2, above 86400 or not a number gives `400` `seconds_invalid`; `sound` and `enabled` become
  booleans; `from` and `until` are dates cut to 10 characters. An unknown `file`
  still replies `{ok: true}` and changes nothing.
- `/api/settings`: `qrUrl` and `qrUrl2` are cut to 500 characters; `transition`
  is `cut` or else `fade`; `fit` is `landscape` (default), `portrait`, `portrait270`
  or `fill`; an empty `label` deletes the label.
- `/api/devices`: `cached_items` is `<downloaded>/<total>`, the number of playlist items (not megabytes) the player has in its cache, reported by the player; empty when unknown. It was called `mem_mb` before.
- Every data change increments the screen's `version`, which is how the player notices it.

## Upload

`POST /api/upload?device=<screen>`, `multipart/form-data`. Each part with a file
name is one media file. Always pass `device`: without it the upload goes to the screen `default` and creates it if it does not exist, which is rarely what you want (the name is guessable).

```bash
curl -u <user>:<password> -H "Idempotency-Key: 9f1c2a-photo-001" \
  -F "file=@photo.jpg" -F "file=@clip.mp4" \
  "https://<host>/api/upload?device=<screen>"
```

- Extensions: `.jpg .jpeg .png .webp .gif` (image, default 10 s) and
  `.mp4 .m4v .webm .mov` (video, default 30 s, muted). H.264 `.mov` is
  repackaged losslessly to MP4; HEIC is rejected (`heic_unsupported`). Content
  is validated (`media_invalid`); video above a safe resolution only warns.
- A new item is `enabled: true`, `sound: false`, without a schedule.
- Limits: `SIGNAGE_MAX_FILE_MB` (100), `SIGNAGE_MAX_ITEMS` per screen (50),
  `SIGNAGE_MAX_TOTAL_MB` (2000); the request length is checked against
  `SIGNAGE_MAX_TOTAL_MB` up front (`batch_too_large`, 413).
- At most 3 uploads at once; others get `503` `server_busy` with `Retry-After: 5`.
  Per-IP rate limit: `429` `too_many_uploads`.
- `Idempotency-Key` (optional, `[A-Za-z0-9_.:-]{1,128}`, scoped to the screen): a
  successful reply is kept for `SIGNAGE_IDEM_TTL` s (600) and a repeat with the
  same key returns it with `Idempotent-Replay: true` without saving again.
  While it runs: `409` `upload_in_progress`. Failed attempts are not stored.
- Success: `{"ok": true, "saved": ["photo.jpg"], "errors": [], "warnings": [], "fixes": []}`. Entries are `<file>: <text>` in the `?lang=` language.
- With errors: `ok: false`, `errors`, `error_codes` (parallel codes) and `code`
  (the first). A batch is processed file by file, so HTTP 200 can carry both
  `saved` and `errors`. If nothing was saved because of load or space: `503` or `507`.
- Whole-request errors have the same shape: `400` `upload_corrupt`, `413`
  `file_too_large` / `batch_too_large`, `507` `file_save_failed`, `429`, `409`,
  `503`. On early replies the server reads a bounded part of the body before
  closing so the client sees the reply, not a reset.
- An invalid `?device=` gives `400` `invalid_device` and creates nothing.

## Item schedule

`schedule` in `POST /api/item` limits when an item plays and comes back in the
playlist as `items[].schedule`.

```json
{"device": "<screen>", "file": "photo.jpg",
 "schedule": {"days": [0, 1, 2, 3, 4], "from": "08:00", "to": "16:00"}}
```

- `days`: unique integers 0 (Monday) to 6 (Sunday).
- `from`, `to`: `HH:MM` (24 h), only together and different. `from` > `to`
  crosses midnight and belongs to the day it starts; `to` is exclusive.
- Other keys are an error (`schedule_invalid`, 400).
- `null`, `{}` and `{"days": []}` without times delete the schedule; an absent
  field leaves it unchanged.
- The player evaluates it against the device clock; the server does not.

## Brand

`brand` in `POST /api/settings` is an object with the keys `name` (max 60),
`email` (120), `phone` (40), `city` (80), `url` (500), `qrEmail` (120),
`pwaTitle` (30); unknown keys are ignored.

- Values are text without `<`, `>` or control characters. An empty string returns
  the field to the default; `"brand": null` deletes the whole brand. New values
  are merged with stored ones.
- `email`, `qrEmail`: an e-mail, optionally with `mailto:`. `url`: `http(s)://`,
  `mailto:` or a bare e-mail.
- Precedence: the screen's brand, then `SIGNAGE_BRAND_*`, then built-in defaults.
  The effective brand and QR cards come back in the playlist `settings`
  (`brand`, `qrCards`, `qrCorner`, `qr1On`, `qr2On`, `qrUrl`, `qrUrl2`, `transition`, `fit`).

## Kiosk update

The release lives in `SIGNAGE_KIOSK_DIR` (default `kiosk/` next to the state
file, `/data/kiosk` in the container): `signage-kiosk.apk` and `kiosk.json`
= `{"versionCode": int > 0, "versionName": "1-32 characters", "sha256": "<64 lowercase hex>", "size": int}`.
A release is valid only if the JSON validates and `size` matches the APK;
otherwise the server acts as if there were none. Publish with
`deploy/tools/publish-kiosk.sh`.

The admin sends `POST /api/screens/<screen>/kiosk-update`, which stores
`kiosk_update = versionCode`. On its next poll the box gets `requested: true`,
downloads the APK, verifies `sha256` and lets the install be confirmed on the TV.
The request clears when the box reports `have >= requested`, or a `state` of
`declined`, `failed` or `no_permission` (these three only count from the second poll
after the request). A poll for an unknown screen writes nothing.

### Box health

The kiosk adds these query parameters to the same poll (an older client may omit them). The server
validates them, treats invalid or missing ones as unknown (`null`), keeps them in
memory only and never answers 400. The SSID is never sent.

| Query | Meaning | Valid values |
|---|---|---|
| `ram`, `ram_total` | free and total RAM in MB | integer 0-65536 |
| `ram_low` | system reports low memory | `1` / `0` |
| `up` | box uptime in s | integer 0-10^9 |
| `rssi` | Wi-Fi signal in dBm; only with active Wi-Fi | integer -127-0 |
| `net` | active network | `wifi`, `eth`, `other` |
| `crash` | WebView renderer crashes since the process started | integer 0-10^6 |

A poll with at least one valid value is a full snapshot (missing becomes `null`);
a poll without them (older kiosk) leaves the snapshot unchanged. `GET /api/devices`
shows `kiosk_ram`, `kiosk_ram_low`, `kiosk_ram_total`, `kiosk_up`, `kiosk_rssi`,
`kiosk_net`, `kiosk_crash`, `kiosk_seen` (epoch of the last report),
`kiosk_boots` (restarts seen as a drop of `up`, memory only) and
`kiosk_last_boot` (epoch of power-on).

Telegram alert: 3 consecutive polls (`RAM_ALERT_POLLS`) with `ram` below 80 MB
(`RAM_ALERT_MB`) raise "persistently low free RAM", cleared at 120 MB
(`RAM_OK_MB`). Only for an online screen.

## Rate limits

Sliding window per client IP; on excess `429` with `Retry-After`,
`Connection: close` and a JSON `code`. Memory is bounded by `SIGNAGE_RL_MAX_KEYS`.

| What | Variables (limit / window in s) | Default | Code |
|---|---|---|---|
| failed logins | `SIGNAGE_RL_LOGIN_MAX` / `_WINDOW` | 10 / 300 | `too_many_attempts` |
| `POST /api/pair/request` | `SIGNAGE_RL_PAIR_MAX` / `_WINDOW` | 30 / 300 | `too_many_requests` |
| `POST /api/upload` | `SIGNAGE_RL_UPLOAD_MAX` / `_WINDOW` | 30 / 60 | `too_many_uploads` |
| `GET /api/kiosk/release` | `SIGNAGE_RL_KIOSK_MAX` / `_WINDOW` | 120 / 60 | `too_many_requests` |

Behind a reverse proxy set `SIGNAGE_TRUST_PROXY=1`: the IP is then the last
`X-Forwarded-For` entry. Without a proxy leave it unset, or anyone can forge the header.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `SIGNAGE_ADMIN_USER`, `SIGNAGE_ADMIN_PASS` | required | basic auth; the server exits with code 2 without them |
| `SIGNAGE_BUILD` | `dev` | version in `/api/health` and the playlist (`build`) |
| `SIGNAGE_MEDIA`, `SIGNAGE_STATE` | `tv-media`, `tv-state.json` next to the app | media directory, state file |
| `SIGNAGE_STATE_BACKUP_RETENTION` | 5 | kept copies of a corrupt state file |
| `SIGNAGE_PUBLIC_URL` | from `Host` / `X-Forwarded-Proto` | public base URL for QR codes |
| `SIGNAGE_CORS_ORIGIN` | empty (off) | the single allowed web origin |
| `SIGNAGE_MAX_FILE_MB`, `SIGNAGE_MAX_ITEMS`, `SIGNAGE_MAX_TOTAL_MB` | 100, 50, 2000 | upload limits |
| `SIGNAGE_TRUST_PROXY` | off | `1` = IP from `X-Forwarded-For` |
| `SIGNAGE_BIND` | `0.0.0.0` | listen address |
| `SIGNAGE_RL_LOGIN_*`, `_PAIR_*`, `_UPLOAD_*`, `_KIOSK_*` | see Rate limits | limits |
| `SIGNAGE_RL_MAX_KEYS` | 4096 | cap on tracked IPs |
| `SIGNAGE_IDEM_TTL`, `SIGNAGE_IDEM_MAX` | 600, 256 | lifetime (s) and count of stored idempotent replies |
| `SIGNAGE_STALE_TMP_SECONDS` | 3600 | age of orphaned temporary files removed at start |
| `SIGNAGE_KIOSK_DIR` | `kiosk/` next to the state | kiosk release directory |
| `SIGNAGE_TG_TOKEN`, `SIGNAGE_TG_CHAT` | empty | Telegram alerts, on only if both are set |
| `SIGNAGE_ALERT_OFFLINE_MIN` | 10 | minutes offline before an alert |
| `SIGNAGE_TELEMETRY`, `_RETENTION_DAYS`, `_MAX_MB` | `telemetry.jsonl` next to the state, 14, 8 | telemetry file, retention, size cap |
| `SIGNAGE_BRAND_*`, `SIGNAGE_BRAND_FILE`, `SIGNAGE_ASSETS_DIR` | neutral demo values | default brand, see [install.md](install.md#configuration) |

An invalid or non-positive numeric value is silently replaced by the default.
The port is the first command-line argument (default 8090).
