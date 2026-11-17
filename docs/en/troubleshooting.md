# Troubleshooting and recovery

[Česky](../cs/troubleshooting.md)

Placeholders: `<host>`, `<ssh-host>`, `<box-ip>`, `<screen>`, `<user>`/`<password>`.
Host paths: data `/opt/signage/data`, backups `/opt/signage/backups`, container
`signage`.

Start with:

```bash
curl -s https://<host>/api/health                      # ok, state_ok, blank_screens, alerts_enabled, autoconvert
curl -s https://<host>/api/tv/<screen>/playlist        # has "items"?
curl -s -u '<user>:<password>' https://<host>/api/devices   # online, blank, render_err, h264, kiosk
ssh <ssh-host> 'docker logs signage 2>&1 | tail -50'
```

## Port 8080 is already in use

`docker compose up` fails with "port is already allocated" or "address already in
use". Set `SIGNAGE_PORT=<free port>` in `.env`, run `docker compose up -d` again
and use that port in every URL (`http://localhost:<port>/admin`).

## A screen is offline

- Power, network or box down. Online means heard from in the last 45 s; only the player sends heartbeats.
- For a box, try `adb connect <box-ip>:5555` or the diagnostics (MENU/INFO on the remote).
- The screen does not exist in the admin, or the URL has a typo. A heartbeat for an unknown name is ignored (200, nothing created).
- Look for `<screen>` in `/api/devices`; create it with **+ New screen**.
- The kiosk was built for another address; it accepts only `https://` and exactly the host of `KIOSK_BASE_URL`.
- Check MENU/INFO "last error" or `adb logcat` filtered on `SignageKiosk`, and rebuild ([kiosk.md](kiosk.md#build)).
- Server or proxy down: `curl` the health URL, `ssh <ssh-host> 'docker ps -a'`, `docker logs signage`.
- During a server outage players keep playing from the offline cache, so "offline" does not mean a black screen.
- The first load without internet does not work.

## Black or frozen screen

- Empty playlist is a legitimate state (the heartbeat reports `ok=1`). Check `items`, upload content, check item schedules (device clock, date and time zone).
- The player draws nothing (image or video error, browser without H.264). Check `blank_screens` in `/api/health` and `blank`, `screen`, `render_err`, `render_why`, `h264` in `/api/devices`. Telegram sends a `[WARNING]` message after more than 2 min without rendering. A missing `h264` means unknown, not "cannot".
- Video stalls: use a smaller resolution or H.264 MP4 via `./deploy/convert-video.sh INPUT [OUTPUT]`.
- Something covers the kiosk: the watchdog brings it back after 20 s, but only with `SYSTEM_ALERT_WINDOW` (see the kiosk section below).
- One-off refresh of a kiosk: `adb shell am start -n app.plainsignage.kiosk/.MainActivity`.

## Video does not play

- H.265 or AV1 are rejected with `media_invalid`, as are images over 100 Mpx or 20,000 px and content that does not match its extension. Convert to H.264 MP4 with `./deploy/convert-video.sh INPUT [OUTPUT]` (non-zero exit when the result fails its own checks) or `ffmpeg -i in -c:v libx264 -profile:v baseline -c:a aac -movflags +faststart out.mp4`.
- `.heic`, `.mkv`, `.avi`, `.mts` and the like give `heic_unsupported` or `video_format_unsupported`: export photos as JPG, video as MP4 (H.264).
- A bad container (QuickTime brand, `moov` after the data, typical for phone `.mov`) is repaired by repackaging (`-c copy`, extension becomes `.mp4`, `fixes` in the reply), only when ffmpeg is present (`"autoconvert": true` in `/api/health`). For files already on disk:

  ```bash
  ssh <ssh-host> docker exec signage python3 fix_media.py --dry-run
  ssh <ssh-host> docker exec signage python3 fix_media.py
  ```

  It repackages in place (atomic overwrite, checks the video track stays bit-identical, never renames) and does nothing without ffmpeg.
- A video over 5 MB without faststart, or a resolution a screen cannot handle, only warns (`warnings`); the file was saved.
- Very old panels have no service worker and need H.264 MP4.

## Upload fails

The reply is JSON with `code` and a localized message. Check `docker logs signage`.

| HTTP | `code` | Cause and fix |
|---|---|---|
| 503 + `Retry-After: 5` | `server_busy` | Three uploads at once, or two conversions running. Wait and retry. |
| 507 | `file_save_failed` | Disk full. Free space (see Full disk) and retry. Leftover `.signage-upload-*` files older than `SIGNAGE_STALE_TMP_SECONDS` are removed at start. |
| 429 + `Retry-After` | `too_many_uploads` | 30 per 60 s per IP. Wait; if everyone gets 429, see Wrong password and rate limits. |
| 413 | `file_too_large` | Over `SIGNAGE_MAX_FILE_MB`. Behind nginx `client_max_body_size` must be higher, or nginx returns 413 first. |
| 413 | `batch_too_large` | Over `SIGNAGE_MAX_TOTAL_MB`. Upload in parts. |
| 200, `ok: false` | `storage_quota`, `item_limit` | Total storage or 50 items per screen. Delete items in the admin; limits are in `/api/health`. |
| 400 | `upload_corrupt` | Damaged or interrupted upload, or form text over 1 MB. Retry. |
| 409 | `upload_in_progress` | The same `Idempotency-Key` is still running. Wait 5 s. |

Changing limits means recreating the container with different `-e SIGNAGE_*`
(`deploy.sh` takes the environment of the existing container).

## The kiosk does not start after a power cut

- Not the HOME app. `set-home-activity` reports `Success` but the box ends at the Android TV launcher. Fix (reversible):

  ```bash
  adb -s <box-ip>:5555 shell cmd package set-home-activity app.plainsignage.kiosk/.MainActivity
  adb -s <box-ip>:5555 shell pm disable-user --user 0 com.google.android.tvlauncher
  # back: adb -s <box-ip>:5555 shell pm enable com.google.android.tvlauncher
  ```

- `SYSTEM_ALERT_WINDOW` missing: another app covers the kiosk and it does not return; the log shows "isBgStartWhitelisted: false" every 20 s. The grant survives `install -r` but not `pm clear` or `adb uninstall`.

  ```bash
  adb -s <box-ip>:5555 shell appops get app.plainsignage.kiosk SYSTEM_ALERT_WINDOW     # expect: allow
  adb -s <box-ip>:5555 shell appops set app.plainsignage.kiosk SYSTEM_ALERT_WINDOW allow
  ```

- The watchdog is paused on purpose: double BACK within 2 s pauses it for 10 minutes.
- No network after boot: the kiosk shows an error and retries (every 5 s, growing to 60 s for network errors and HTTP 5xx). Check Wi-Fi or cable and `KIOSK_BASE_URL`.
- ADB unreachable: on some boxes `adb connect` disappears after a restart; turn USB debugging on again and do the whole install in one session.
- APK install fails (`INSTALL_FAILED_UPDATE_INCOMPATIBLE`): a different signing key or an unchanged `versionCode`. Raise the version in `kiosk-app/AndroidManifest.xml` and use the same keystore; otherwise `adb uninstall app.plainsignage.kiosk` first and grant the permission again.
- No ADB and cannot leave the kiosk: press BACK twice within 2 s.
- Screensaver and sleep: see the settings in [kiosk.md](kiosk.md#preparing-a-box) (never `-1`).
- A cold start after unplugging power and a TV power cycle are not verified; test them physically.

## New version, cache and service worker

- "New version, reloading" is normal: after a deploy the playlist carries a higher `build` and the player reloads on a transition between items, within 2 minutes. During an outage the cached playlist has the old build and no reload happens.
- "Offline, playing the saved copy": the player plays from Cache Storage. A playlist becomes an offline bundle only after all media are downloaded, so open `/tv/<screen>` online, wait for the download, then test offline.
- The screen keeps showing old code: compare `version` in `/api/health` with the last `deploy.sh` build, then restart the player or hard-reload.
- Cache full or failing saves: the player sends `quota` (free space) only when something already fails; see `storage_mb` in `/api/devices`. Stored content is not deleted when space runs out. Caches are `signage-media-v1`, `signage-shell-v2`, `signage-playlists-v1`; older `signage-*` caches are deleted on activation. HTML is served `no-store`.

## Wrong password and rate limits

- 401: wrong or missing basic auth. Check `curl -s -o /dev/null -w '%{http_code}\n' -u '<user>:<password>' https://<host>/admin` (200). Non-ASCII passwords need `PYTHONUTF8=1`.
- 429 with `Retry-After`: 10 failed logins per 300 s per IP, `pair/request` 30 per 300 s, upload 30 per 60 s (`SIGNAGE_RL_*`). Wait, or `ssh <ssh-host> docker restart signage` (limits live in memory only; this also clears the idempotency cache and briefly interrupts the service, players keep playing from cache). Check `docker logs signage` and the proxy log first to rule out an attack; a permanent block belongs in the firewall.
- 429 for everybody at once: `SIGNAGE_TRUST_PROXY=1` is missing or the proxy does not send `X-Forwarded-For`. With `=1` and no proxy anyone can forge IPs. Check (prints a count):

  ```bash
  ssh <ssh-host> "docker inspect signage --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -c '^SIGNAGE_TRUST_PROXY=1\$'"
  ```

  `deploy.sh` adds the variable if it is missing.
- Pairing: a code is valid for 10 minutes; an invalid or used code returns 404 or 409. Create a new one on the TV.

## Full disk

Symptoms: HTTP 507, `file_save_failed`, `backup.sh` reports low space.

1. `ssh <ssh-host> 'df -h /opt/signage; du -sh /opt/signage/*'`
2. Free what is not needed: old backups (keep the last verified one), old images (`docker images signage`, `docker image prune`), `releases/`, `data.broken-*` after a successful restore, logs.
3. Do not delete media or `tv-state.json` by hand; delete through the admin.
4. Check `/api/health` (state writes are atomic) and run `ssh <ssh-host> /opt/signage/backup.sh`.

Docker logs rotate (10 MB x 3) when the container was created with the `--log-opt` flags from [install.md](install.md#docker-compose), backups keep 14, `deploy.sh` keeps 5 images. If
`/api/health` shows `state_error`, the state is corrupt, not the disk.

## Corrupt state file

Symptom: `/api/health` is not `ok` and shows `state_error`, writes are blocked.
When the server loads an unreadable file it copies it next to the state file as
`tv-state.json.corrupt-<YYYYMMDD-HHMMSS>-<n>.bak` (mode 600, last 5 kept,
`SIGNAGE_STATE_BACKUP_RETENTION`); if that copy fails, writes stay blocked so
nothing is overwritten.

1. `docker logs signage 2>&1 | grep -iE "state|backup|error"`
2. `python3 -m json.tool /opt/signage/data/tv-state.json`, and the same on the `.corrupt-*.bak`.
3. `docker stop signage`, restore the last good state (next two sections), `docker start signage`, check `/api/health`.

## Restore from a data backup

Daily `signage-data-<STAMP>.tgz` in `/opt/signage/backups`, last 14 kept
(set up in [install.md](install.md#backups)).

```bash
scp deploy/restore.sh <ssh-host>:/opt/signage/restore.sh && ssh <ssh-host> chmod +x /opt/signage/restore.sh
ssh <ssh-host>
/opt/signage/restore.sh --dry-run latest          # only prints what it would do
/opt/signage/restore.sh latest                    # newest backup
/opt/signage/restore.sh /opt/signage/backups/signage-data-<STAMP>.tgz
```

The script verifies the archive (`tar -tzf`, valid `tv-state.json`, free space),
stops the container, moves the current data to `/opt/signage/data.broken-<stamp>`
(never deletes), unpacks, verifies and starts the container. On any error it puts
the original directory back and exits non-zero. It shares a lock with
`backup.sh`; paths can be changed with `SIGNAGE_DATA_DIR`, `SIGNAGE_BACKUP_DIR`,
`SIGNAGE_CONTAINER` (`SIGNAGE_RESTORE_SKIP_DOCKER=1` leaves docker alone). Then
check `/api/health` and that screens report in. If the restore was a mistake:

```bash
docker stop signage
mv /opt/signage/data /opt/signage/data.restored-$(date +%Y%m%d-%H%M%S)
mv /opt/signage/data.broken-<STAMP> /opt/signage/data
docker start signage
```

Without Docker: the script only stops a Docker container, so a running
`tvserver.py` would overwrite the restored state. Stop it first, restore with
docker untouched, then start the server again:

```bash
# stop tvserver.py (Ctrl+C or kill), then:
SIGNAGE_RESTORE_SKIP_DOCKER=1 SIGNAGE_DATA_DIR=./data SIGNAGE_BACKUP_DIR=./backups ./deploy/restore.sh latest
# start tvserver.py again, see install.md "Without Docker"
```

Delete `data.broken-*` only after verifying operation. Verified only by
`deploy/test-backup-restore.sh`, not on a live service.

## Restore the pre-deploy snapshot

Before switching, `deploy.sh` saves `tv-state.json` to
`/opt/signage/backups/tv-state.json.pre-<release>` (release =
`timestamp-g<gitSHA>[-dirty]`, last 5 kept). It restores state only, not media or code.

```bash
docker stop signage
cp -p /opt/signage/backups/tv-state.json.pre-<release> /opt/signage/data/tv-state.json
docker start signage
```

## Deploy and rollback

- `deploy.sh` fails before the switch (tests, canary, health): production is unchanged. Read the output and `docker logs`. Typical: `docker inspect signage` fails because the container does not exist yet (the script cannot do a first install, see [install.md](install.md#production-deployment)); `pytest: command not found`.
- Fails after the switch: the script rolls back itself (the old container was only renamed; otherwise image `signage:rollback-<release>`). If that fails too, check `docker ps -a`, `docker images signage` and recreate `signage` by hand with the original `docker run` and that image.
- A rollback returns code, not data; restore state from a snapshot.
- After a deploy players update within 2 minutes; a remaining error is a player cached on old code. Verify with `BASE=https://<host> AUSER='<user>' APASS='<password>' ./deploy/smoke-test.sh`. The header test (HSTS, `X-Frame-Options`, `X-Content-Type-Options`) is red behind a bare nginx; that is the proxy, not the service.

## Rotate credentials

- To rotate the admin password, generate a new one (`openssl rand -base64 24`), recreate the container with the new `-e SIGNAGE_ADMIN_PASS=...` (a normal deploy then keeps it), verify that the old login gives 401 and the new one 200. Never commit it or leave it in shell history.
- Telegram token: `/revoke` at @BotFather (the old token stops at once), recreate the container with `-e SIGNAGE_TG_TOKEN=<TOKEN> -e SIGNAGE_TG_CHAT=<chat id>`, check `alerts_enabled: true` in `/api/health`. The deploy canary never has Telegram credentials.
- The kiosk signing keystore has no rotation: losing it means reinstalling every box after `adb uninstall`. Back it up.

## Telegram alert does not arrive

Alerting is on only with both `SIGNAGE_TG_TOKEN` and `SIGNAGE_TG_CHAT`.

1. `alerts_enabled` in `/api/health` must be true; otherwise recreate the container with both. The `chat.id` comes from `https://api.telegram.org/bot<TOKEN>/getUpdates` after messaging the bot.
2. `ssh <ssh-host> 'docker logs signage 2>&1 | grep "alert: telegram"'` (the token is masked). Causes: invalid or revoked token, wrong chat id, the bot has no conversation with the chat (message it first), or no route to `api.telegram.org`. Failed messages are retried every minute.
3. Offline alerts come after `SIGNAGE_ALERT_OFFLINE_MIN` (10) minutes; after a restart only screens heard from since start are reported. Blank (`[WARNING]`) and offline (`[CRITICAL]`) are not reported together.

## Unverified

- Cold start of a box after unplugging power, a TV power cycle, and the watchdog during a real network outage.
- `restore.sh` and `backup.sh` on a live service; only the test `deploy/test-backup-restore.sh` was run.
