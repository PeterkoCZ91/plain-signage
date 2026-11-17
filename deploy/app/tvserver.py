#!/usr/bin/env python3
"""Signage server for smart TVs (and anything with a browser).

Model: a screen (smart TV) opens  http://server/tv/<device>  and pulls its
own playlist. The end user manages content at  http://server/admin .

One server, one playlist per device. No DLNA, no rotation - a modern
browser handles images and video directly.

  ./tvserver.py [port]        default 8090
"""
import base64
import copy
import errno
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import stat
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.parse
from collections import OrderedDict
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import video_convert
from statefmt import (STATE_SCHEMA_VERSION, DEVICE_RE, _empty_state, _migrate_state, _normalize_state, valid_device)  # noqa: F401 (re-export)
from mediautil import (IMG_EXT, VID_EXT, ALLOWED_EXT, ALLOWED_DESC, _HEIC_EXT, _VIDEO_EXT_UNSUPPORTED, unsupported_msg, kind, safe, _media_name, _range_bounds, _MapView, _mapped, ConvertBusy, ConvertNoSpace, _unlink_quiet)  # noqa: F401 (re-export)
from alerts import (BLANK_AFTER_S, _render_report, is_blank, alert_events, format_alert, _kiosk_health, kiosk_health_update, KIOSK_HEALTH_FIELDS)  # noqa: F401 (re-export)
from mediacheck import (_IMG_MAGIC, BAD_VIDEO_CODECS, MP4_BRANDS, _MP4_CONTAINERS, _MP4_TOP_BOXES, SAFE_VIDEO_LONG, SAFE_VIDEO_SHORT, SAFE_IMAGE_MPX, HARD_IMAGE_MPX, MAX_IMAGE_SIDE, FASTSTART_MIN_BYTES, JPEG_HEADER_SCAN, _JPEG_SOF, _mp4_brands, _mp4_boxes, _stsd_entries, _mp4_sample_formats, _tkhd_size, _visual_entry_size, _biggest, _mp4_size, _jpeg_size, _png_size, _gif_size, _webp_size, _image_size, _top_box_order, _moov_after_mdat, _faststart_warning, _image_hard_limit, validate_media)  # noqa: F401 (re-export)
from ratelimit import (RateLimiter, TtlCache)  # noqa: F401 (re-export)
from brand import (BRAND_DEFAULTS, BRAND_MAX, DEFAULT_QR_URL, DEFAULT_QR_EMAIL, EMAIL_RE, brand_of, validate_brand, qr_target, qr_caption, qr_cards, DISPLAY_MODES, display_mode, playlist_settings)  # noqa: F401 (re-export)
from validators import (validate_schedule, clean_schedule, LABEL_MAX, validate_label, validate_kiosk_release)  # noqa: F401 (re-export)
from errors import (ERRORS, SUPPORTED_LANGS, error_text, ErrText, localize, pick_lang)  # noqa: F401 (re-export)
from multipart import FileTooLargeError, MultipartError, parse_multipart

# the admin and write API are protected by basic auth (set via env on the VPS)
ADMIN_USER = os.environ.get("SIGNAGE_ADMIN_USER", "")
ADMIN_PASS = os.environ.get("SIGNAGE_ADMIN_PASS", "")
PLACEHOLDER_ADMIN_PASS = "change-me-to-a-long-random-password"  # literal from .env.example, refused at startup
# /api/devices is behind auth - it enumerates all device names (must not be public)
PROTECTED = ("/admin", "/api/upload", "/api/delete", "/api/order", "/api/item",
             "/api/devices", "/api/settings", "/api/pair/claim", "/api/screens",
             "/api/thumb/")
APP_VERSION = os.environ.get("SIGNAGE_BUILD", "dev")   # set at deploy time

try:
    import segno   # QR generator (pure Python)
except ImportError:
    segno = None
QR_CACHE_MAX = 256
_qr_cache = OrderedDict()                                # url -> PNG bytes (LRU, max QR_CACHE_MAX)


def _qr_cache_get(key):
    png = _qr_cache.get(key)
    if png is not None:
        try:
            _qr_cache.move_to_end(key)
        except KeyError:
            pass
    return png


def _qr_cache_put(key, png):
    _qr_cache[key] = png
    _qr_cache.move_to_end(key)
    while len(_qr_cache) > QR_CACHE_MAX:
        try:
            _qr_cache.popitem(last=False)
        except KeyError:
            break

ROOT = os.path.dirname(os.path.abspath(__file__))
TVDIR = os.path.join(ROOT, "tv")
MEDIA = os.environ.get("SIGNAGE_MEDIA", os.path.join(ROOT, "tv-media"))
STATE = os.environ.get("SIGNAGE_STATE", os.path.join(ROOT, "tv-state.json"))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8090
BIND = os.environ.get("SIGNAGE_BIND", "0.0.0.0")  # behind nginx on the same host 127.0.0.1 works
MAX_UPLOAD = 200 * 1024 * 1024
MAX_JSON_BODY = 64 * 1024                  # body cap for JSON endpoints (upload has its own path)
MAX_ITEM_SECONDS = 24 * 3600
UPLOAD_DRAIN_MAX = 512 * 1024 * 1024        # how much of a rejected upload's remainder to drain so the client sees 413
MAX_CONCURRENT_UPLOADS = 3                 # further uploads get 503 + Retry-After
_upload_slots = threading.BoundedSemaphore(MAX_CONCURRENT_UPLOADS)
CORS_ORIGIN = os.environ.get("SIGNAGE_CORS_ORIGIN", "").strip().rstrip("/")


def _cors_headers(origin):
    """Allow CORS only for the exactly configured web origin."""
    if not CORS_ORIGIN or not origin or origin != CORS_ORIGIN:
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Vary": "Origin",
    }



def _env_int(key, default):
    """Read a positive integer from env, else return default (robust against garbage)."""
    try:
        val = int(os.environ.get(key, str(default)))
        return val if val > 0 else default
    except (ValueError, TypeError):
        return default


STATE_BACKUP_RETENTION = _env_int("SIGNAGE_STATE_BACKUP_RETENTION", 5)


# Configurable upload limits (env with sensible defaults for demos/presentations)
MAX_FILE_MB = _env_int("SIGNAGE_MAX_FILE_MB", 100)      # max size of a single file
MAX_ITEMS = _env_int("SIGNAGE_MAX_ITEMS", 50)           # max items per device
MAX_TOTAL_MB = _env_int("SIGNAGE_MAX_TOTAL_MB", 2000)   # max total storage size
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024
MAX_TOTAL_BYTES = MAX_TOTAL_MB * 1024 * 1024
PAIR_TTL_SECONDS = 10 * 60
PAIR_CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
# Alerting via a Telegram bot - without token+chat all alerting is off
ALERT_TG_TOKEN = os.environ.get("SIGNAGE_TG_TOKEN", "")
ALERT_TG_CHAT = os.environ.get("SIGNAGE_TG_CHAT", "")
ALERT_OFFLINE_MIN = _env_int("SIGNAGE_ALERT_OFFLINE_MIN", 10)
ALERT_CHECK_SECONDS = 60
TELEMETRY = os.environ.get("SIGNAGE_TELEMETRY", os.path.join(os.path.dirname(STATE), "telemetry.jsonl"))
TELEMETRY_RETENTION_DAYS = _env_int("SIGNAGE_TELEMETRY_RETENTION_DAYS", 14)
TELEMETRY_MAX_BYTES = _env_int("SIGNAGE_TELEMETRY_MAX_MB", 8) * 1024 * 1024
AVAILABILITY_MAX_HOURS = 7 * 24
# Managed update of the Android kiosk (app.plainsignage.kiosk): APK + release descriptor.
# A release is valid only when kiosk.json passes validation and size matches the APK.
KIOSK_DIR = os.environ.get("SIGNAGE_KIOSK_DIR", os.path.join(os.path.dirname(STATE), "kiosk"))
# Optional deployment layer: files here (default-qr.png, icons, manifest.webmanifest)
# replace the built-in neutral ones of the same name under /assets/.
ASSETS_DIR = os.environ.get("SIGNAGE_ASSETS_DIR", os.path.join(os.path.dirname(STATE), "assets"))


def asset_path(name):
    """Overlay file from the data directory if present, else the built-in asset."""
    name = os.path.basename(name)
    custom = os.path.join(ASSETS_DIR, name)
    if os.path.isfile(custom):
        return custom
    return os.path.join(TVDIR, "assets", name)
KIOSK_APK = "signage-kiosk.apk"
KIOSK_JSON = "kiosk.json"
KIOSK_URL = "/kiosk/" + KIOSK_APK
KIOSK_JSON_MAX = 4096
KIOSK_MIME = "application/vnd.android.package-archive"
KIOSK_STATES = ("idle", "downloading", "waiting", "installed", "declined", "failed", "no_permission")
KIOSK_RESOLVING = ("declined", "failed", "no_permission")   # the box closed the request without installing

_lock = threading.RLock()
_telemetry_lock = threading.Lock()             # telemetry file only; never held together with _lock
_state = {"devices": {}, "pairs": {}}          # {device: {"version": int, "items": [ {id,file,type,seconds} ]}}
_online = {}
_telemetry_pruned_at = 0
_availability_cache = {}
_uploading_names = set()
_uploading_bytes = 0


# ---------- abuse protection: rate limiting, idempotency ----------
TRUST_PROXY = os.environ.get("SIGNAGE_TRUST_PROXY", "") == "1"
LOGIN_FAIL_MAX = _env_int("SIGNAGE_RL_LOGIN_MAX", 10)              # failed logins per IP
LOGIN_FAIL_WINDOW = _env_int("SIGNAGE_RL_LOGIN_WINDOW", 300)       # ...within this many seconds
PAIR_REQUEST_MAX = _env_int("SIGNAGE_RL_PAIR_MAX", 30)
PAIR_REQUEST_WINDOW = _env_int("SIGNAGE_RL_PAIR_WINDOW", 300)
UPLOAD_MAX = _env_int("SIGNAGE_RL_UPLOAD_MAX", 30)
UPLOAD_WINDOW = _env_int("SIGNAGE_RL_UPLOAD_WINDOW", 60)
# a box polls once a minute; the cap is per IP, several boxes may sit behind one NAT
KIOSK_POLL_MAX = _env_int("SIGNAGE_RL_KIOSK_MAX", 120)
KIOSK_POLL_WINDOW = _env_int("SIGNAGE_RL_KIOSK_WINDOW", 60)
RL_MAX_KEYS = _env_int("SIGNAGE_RL_MAX_KEYS", 4096)                # cap on the number of tracked IPs
IDEM_TTL = _env_int("SIGNAGE_IDEM_TTL", 600)
IDEM_MAX_ITEMS = _env_int("SIGNAGE_IDEM_MAX", 256)
IDEM_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
STALE_TMP_SECONDS = _env_int("SIGNAGE_STALE_TMP_SECONDS", 3600)
STALE_TMP_PREFIXES = (".signage-upload-", ".oprava-")
STATE_TMP_PREFIX = ".signage-state-"



_login_limiter = RateLimiter(LOGIN_FAIL_MAX, LOGIN_FAIL_WINDOW, max_keys=RL_MAX_KEYS)
_pair_limiter = RateLimiter(PAIR_REQUEST_MAX, PAIR_REQUEST_WINDOW, max_keys=RL_MAX_KEYS)
_upload_limiter = RateLimiter(UPLOAD_MAX, UPLOAD_WINDOW, max_keys=RL_MAX_KEYS)
_kiosk_limiter = RateLimiter(KIOSK_POLL_MAX, KIOSK_POLL_WINDOW, max_keys=RL_MAX_KEYS)
_idem_cache = TtlCache(IDEM_TTL, IDEM_MAX_ITEMS)
_IDEM_BUSY = object()


def reset_rate_limits():
    for lim in (_login_limiter, _pair_limiter, _upload_limiter, _kiosk_limiter):
        lim.clear()


def _idem_begin(dev, key):
    """('new'|'busy'|'replay', stored_response). 'new' reserves the key."""
    with _lock:
        cur = _idem_cache.get((dev, key))
        if cur is _IDEM_BUSY:
            return "busy", None
        if cur is not None:
            return "replay", cur
        _idem_cache.put((dev, key), _IDEM_BUSY)
        return "new", None


def _idem_finish(dev, key, result):
    """result = (status, payload) is stored for replay, None cancels the reservation."""
    if result is None:
        _idem_cache.pop((dev, key))
    else:
        _idem_cache.put((dev, key), result)


def cleanup_stale_state_tmp(max_age=None, now=None):
    """Delete orphaned .signage-state-* files (kill -9 in the middle of save()) in the STATE directory.

    Only files with this prefix and older than max_age; never touches
    tv-state.json or the .corrupt-* backups (they lack the prefix)."""
    max_age = STALE_TMP_SECONDS if max_age is None else max_age
    now = time.time() if now is None else now
    removed = 0
    directory = os.path.dirname(os.path.abspath(STATE))
    try:
        names = os.listdir(directory)
    except OSError:
        return 0
    for n in names:
        if not n.startswith(STATE_TMP_PREFIX):
            continue
        p = os.path.join(directory, n)
        try:
            if os.path.isfile(p) and not os.path.islink(p) \
                    and now - os.path.getmtime(p) > max_age:
                os.unlink(p)
                removed += 1
        except OSError:
            pass
    sys.stderr.write("  Cleanup of temporary state files: removed %d\n" % removed)
    return removed


def cleanup_stale_uploads(max_age=None, now=None):
    """Delete old temporary files (.signage-upload-*, .oprava-*) in the media root."""
    max_age = STALE_TMP_SECONDS if max_age is None else max_age
    now = time.time() if now is None else now
    removed = 0
    try:
        names = os.listdir(MEDIA)
    except OSError:
        return 0
    for n in names:
        if not n.startswith(STALE_TMP_PREFIXES):
            continue
        p = os.path.join(MEDIA, n)
        try:
            if os.path.isfile(p) and not os.path.islink(p) \
                    and now - os.path.getmtime(p) > max_age:
                os.unlink(p)
                removed += 1
        except OSError:
            pass
    sys.stderr.write("  Cleanup of temporary uploads: removed %d\n" % removed)
    return removed
_state_recovery_required = False




def _state_backup_paths():
    directory = os.path.dirname(os.path.abspath(STATE))
    prefix = os.path.basename(STATE) + ".corrupt-"
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    return [os.path.join(directory, name) for name in names
            if name.startswith(prefix) and name.endswith(".bak")]


def _prune_state_backups():
    backups = []
    for path in _state_backup_paths():
        try:
            backups.append((os.path.getmtime(path), path))
        except OSError:
            continue
    backups.sort(reverse=True)
    for _, path in backups[STATE_BACKUP_RETENTION:]:
        try:
            os.unlink(path)
        except OSError:
            pass


def _backup_corrupt_state():
    directory = os.path.dirname(os.path.abspath(STATE))
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    prefix = os.path.basename(STATE) + ".corrupt-" + stamp + "-"
    for counter in range(100):
        target = os.path.join(directory, f"{prefix}{counter}.bak")
        fd = None
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with open(STATE, "rb") as source, os.fdopen(fd, "wb") as backup:
                fd = None
                shutil.copyfileobj(source, backup)
                backup.flush()
                os.fsync(backup.fileno())
            _prune_state_backups()
            return target
        except FileExistsError:
            continue
        except OSError:
            if fd is not None:
                os.close(fd)
            try:
                os.unlink(target)
            except OSError:
                pass
            return None
    return None




def load():
    global _state, _state_recovery_required
    _state_recovery_required = False
    try:
        with open(STATE, encoding="utf-8") as fh:
            raw = json.load(fh)
        raw = _migrate_state(raw)
    except FileNotFoundError:
        raw = _empty_state()
    except (json.JSONDecodeError, ValueError):
        backup = _backup_corrupt_state()
        suffix = f"; backup: {backup}" if backup else "; could not create a backup"
        sys.stderr.write("  State file is corrupted, using an empty state" + suffix + "\n")
        _state_recovery_required = backup is None
        raw = _empty_state()
    except OSError as exc:
        sys.stderr.write("  State file cannot be read (%s), keeping an empty state without overwriting\n"
                         % type(exc).__name__)
        _state_recovery_required = True
        raw = _empty_state()
    _state = _normalize_state(raw)


def save():
    if _state_recovery_required:
        raise OSError("state recovery is required before saving")
    _state["schema_version"] = STATE_SCHEMA_VERSION
    directory = os.path.dirname(os.path.abspath(STATE))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=STATE_TMP_PREFIX, dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(_state, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, STATE)
        try:
            dir_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass




def device(dev, exists=None):
    """Device from the state (created if missing). `exists(file)` replaces isfile - the hot
    path (playlist) checks files outside _lock and passes the finished result in."""
    if exists is None:
        def exists(name):
            return os.path.isfile(media_path(name) or "")
    devices = _state.setdefault("devices", {})
    if not isinstance(devices, dict):
        devices = _state["devices"] = {}
    d = devices.setdefault(dev, {"version": 0, "items": []})
    if not isinstance(d, dict):
        d = devices[dev] = {"version": 0, "items": []}
    items = d.get("items") if isinstance(d.get("items"), list) else []
    # drop items whose file has disappeared
    d["items"] = [i for i in items
                   if isinstance(i, dict) and exists(i.get("file"))]
    return d






def new_screen_name(label="", taken=None):
    """Random, unguessable screen name `<slug>-<12 hex>` (48 bit).

    The slug only helps orientation in the URL; the random part provides the
    security. The name is also a valid DEVICE_RE identifier and does not
    collide with an existing one.
    """
    if taken is None:
        taken = _state.get("devices") or {}
    ascii_label = unicodedata.normalize("NFKD", str(label or "")).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_label.lower()).strip("-")[:20].strip("-") or "screen"
    while True:
        name = f"{slug}-{secrets.token_hex(6)}"
        if name not in taken:
            return name


def prune_pairs(now=None):
    now = int(now or time.time())
    pairs = _state.setdefault("pairs", {})
    for code in list(pairs):
        if int(pairs[code].get("expires", 0)) <= now:
            del pairs[code]





def media_path(name):
    """Return the media path only if it (even through a symlink) stays inside MEDIA."""
    if not isinstance(name, str) or not name or os.path.basename(name) != name:
        return None
    path = os.path.join(MEDIA, name)
    root = os.path.realpath(MEDIA)
    try:
        if os.path.commonpath((root, os.path.realpath(path))) != root:
            return None
    except ValueError:
        return None
    return path


def _reserve_upload(name, size):
    """Reserve a unique destination and quota space for one upload."""
    global _uploading_bytes
    with _lock:
        if storage_bytes() + _uploading_bytes + size > MAX_TOTAL_BYTES:
            return None, "quota"
        stem, ext = os.path.splitext(name)
        candidate = name
        n = 1
        while (candidate in _uploading_names
               or os.path.lexists(os.path.join(MEDIA, candidate))):
            candidate = f"{stem}_{n}{ext}"
            n += 1
        path = media_path(candidate)
        if path is None:
            return None, "path"
        _uploading_names.add(candidate)
        _uploading_bytes += size
        return (candidate, path), None


def _release_upload(name, size):
    global _uploading_bytes
    with _lock:
        _uploading_names.discard(name)
        _uploading_bytes = max(0, _uploading_bytes - size)









def _screen_of(dev):
    """Canvas size the device itself reported in its heartbeat."""
    s = (_online.get(dev) or {}).get("screen")
    if not s:
        return None
    w, h = int(s.get("w") or 0), int(s.get("h") or 0)
    return (w, h) if w > 0 and h > 0 else None


def _video_size_warning(size, dev=None):
    """Warning about video resolution. NEVER forbids anything - just tells the truth."""
    if not size:
        return None                             # not measured = stay silent
    w, h = size
    screen = _screen_of(dev)
    if screen:
        # the screen has reported in, so judge by it, not by the estimate
        if max(w, h) <= max(screen) and min(w, h) <= min(screen):
            return None
        return ErrText("warn_video_over_screen", w=w, h=h, dev=dev,
                       sw=screen[0], sh=screen[1])
    if max(w, h) <= SAFE_VIDEO_LONG and min(w, h) <= SAFE_VIDEO_SHORT:
        return None
    return ErrText("warn_video_big", w=w, h=h, long=SAFE_VIDEO_LONG,
                   short=SAFE_VIDEO_SHORT)




def _image_size_warning(size, dev=None):
    """Warning about a needlessly large photo. Never forbids anything."""
    if not size:
        return None
    w, h = size
    if w * h > SAFE_IMAGE_MPX * 1000000:
        mpx = -(-w * h // 100000) / 10.0
        return ErrText("warn_image_big", w=w, h=h, mpx=mpx)
    screen = _screen_of(dev)
    if screen and max(w, h) > 2 * max(screen):
        return ErrText("warn_image_over_screen", w=w, h=h, dev=dev,
                       sw=screen[0], sh=screen[1])
    return None


def media_warnings(name, data, dev=None):
    """Non-binding warnings for a file that otherwise passed validation."""
    ext = os.path.splitext(name)[1].lower()
    out = []
    if ext in IMG_EXT:
        out.append(_image_size_warning(_image_size(ext, data), dev))
    elif ext in (".mp4", ".m4v", ".mov"):
        out.append(_video_size_warning(_mp4_size(data), dev))
        out.append(_faststart_warning(data))
    return [w for w in out if w]



# --- Auto-convert ----------------------------------------------------------
# Most "unsupported" videos actually have a perfectly fine H.264 track and
# only the container is wrong. We can repack it losslessly in a fraction of a
# second, so there is no point sending the customer to transcode something
# that is fine. Transcoding (lossy, tens of seconds) is deliberately not done
# here - see video_convert.py.

def _container_ok(data):
    """True when the MP4 container is what the TV browser expects."""
    if data[4:8] != b"ftyp":
        return False                            # without ftyp we cannot tell the brand
    brands = _mp4_brands(data)
    if brands and not (brands & MP4_BRANDS):
        return False                            # pure QuickTime
    return not _moov_after_mdat(data)


def _repack_reason(name, data):
    """Reason to repack, or None when the container is fine / repacking would not help."""
    if os.path.splitext(name)[1].lower() not in (".mp4", ".m4v", ".mov"):
        return None
    if data[4:8] not in _MP4_TOP_BOXES:
        return None                             # this is not MP4/MOV at all
    if _mp4_sample_formats(data) & BAD_VIDEO_CODECS:
        return None                             # H.265/AV1: -c copy would only carry the problem over
    brands = _mp4_brands(data)
    if brands and not (brands & MP4_BRANDS):
        return "QuickTime obal"
    if _moov_after_mdat(data):
        return "index moov az za daty"
    return None


# keys are the reasons returned by _repack_reason
_FIX_CODES = {"QuickTime obal": "fix_repacked_quicktime",
              "index moov az za daty": "fix_repacked_moov"}


def _fix_note(reason):
    """Note about an automatic fix (ErrText: Czech by default, cs/en by language)."""
    return ErrText(_FIX_CODES.get(reason, "fix_repacked"), reason=reason)


def auto_repack(name, data):
    """Lossless fix of the video container. Returns (name, data, notes).

    When there is nothing to fix, ffmpeg is missing, or the conversion fails,
    the input is returned unchanged - the original validation then decides as
    before. The result is accepted only if the container is really fine, so
    that a file "fixed" in name only never reaches the playlist.
    """
    reason = _repack_reason(name, data)
    if not reason or not video_convert.available():
        return name, data, []
    repacked = video_convert.repack(data, os.path.splitext(name)[1].lower())
    if not repacked or not _container_ok(repacked):
        return name, data, []
    stem = os.path.splitext(name)[0]
    return (stem + ".mp4", repacked, [_fix_note(reason)])




def validate_media_file(name, path):
    """validate_media over a file on disk."""
    with _mapped(path) as data:
        return validate_media(name, data)


def media_warnings_file(name, path, dev=None):
    """media_warnings over a file on disk."""
    with _mapped(path) as data:
        return media_warnings(name, data, dev)




CONVERT_SPACE_RESERVE = 64 * 1024 * 1024        # on top of 2x the input size


def _repack_file(src):
    """ffmpeg -c copy: file -> new temporary .mp4 in MEDIA, or None.

    Raises ConvertBusy (slots busy even after video_convert.CONVERT_WAIT_S) and
    ConvertNoSpace (free space < 2x input + reserve); other failures = None.

    Like video_convert.repack, but without copies to /tmp and without loading the
    data into memory: ffmpeg reads the upload's temporary file directly and
    writes straight into a temporary file in the same directory, so the
    result can be published with an atomic os.replace.
    """
    if not video_convert.FFMPEG:
        return None
    if not video_convert._slots.acquire(timeout=video_convert.CONVERT_WAIT_S):
        raise ConvertBusy()
    dst = None
    try:
        try:
            needed = 2 * os.path.getsize(src) + CONVERT_SPACE_RESERVE
            free = shutil.disk_usage(MEDIA).free
        except OSError:
            needed, free = 0, 0
        if free < needed:
            raise ConvertNoSpace()
        fd, dst = tempfile.mkstemp(prefix=".signage-upload-", dir=MEDIA)
        os.close(fd)
        r = subprocess.run(
            [video_convert.FFMPEG, "-nostdin", "-v", "error", "-y", "-i", src,
             "-map", "0:v:0", "-map", "0:a?", "-c", "copy",
             "-movflags", "+faststart", "-f", "mp4", dst],
            stdin=subprocess.DEVNULL, capture_output=True,
            timeout=video_convert.CONVERT_TIMEOUT_S)
        if r.returncode != 0 or os.path.getsize(dst) == 0:
            raise OSError("conversion failed")
        out, dst = dst, None
        return out
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        video_convert._slots.release()
        if dst:
            _unlink_quiet(dst)




def auto_repack_file(name, path):
    """Auto-convert over a file: returns (name, path, notes).

    When repacking, a NEW temporary file is created (the original stays with
    the caller, who cleans it up); otherwise the input is returned unchanged.
    """
    with _mapped(path) as data:
        reason = _repack_reason(name, data)
    if not reason or not video_convert.available():
        return name, path, []
    repacked = _repack_file(path)
    if not repacked:
        return name, path, []
    with _mapped(repacked) as nd:
        ok = bool(nd) and _container_ok(nd)
    if not ok:
        _unlink_quiet(repacked)
        return name, path, []
    stem = os.path.splitext(name)[0]
    return (stem + ".mp4", repacked, [_fix_note(reason)])


def _file_size(name):
    """Size of one media file in bytes (0 when missing)."""
    try:
        return os.path.getsize(media_path(name) or "")
    except OSError:
        return 0


def storage_bytes():
    """Total size of the media directory in bytes."""
    total = 0
    try:
        for n in os.listdir(MEDIA):
            p = media_path(n)
            if n.startswith(STALE_TMP_PREFIXES):
                continue
            if p and os.path.isfile(p):
                try:
                    total += os.path.getsize(p)
                except OSError:
                    pass
    except OSError:
        pass
    return total


# ---------- media previews (admin) ----------
THUMB_DIR = ".thumbs"          # leading dot = outside the playlist, quota and /media (safe() strips the dot)
THUMB_MAX_PX = 320
THUMB_TIMEOUT_S = 15
_thumb_slots = threading.Semaphore(2)
_THUMB_RE = re.compile(r"^(?P<name>.+)\.\d+-\d+\.jpg$")


def _thumb_dir():
    """Previews directory, or None when it would point outside MEDIA (symlink)."""
    d = os.path.join(MEDIA, THUMB_DIR)
    if os.path.islink(d):
        return None
    return d


def _thumb_key(name, src):
    st = os.stat(src)
    return "%s.%d-%d.jpg" % (name, st.st_mtime_ns, st.st_size)


def _remove_thumbs(name):
    """Delete all previews of one media file."""
    d = _thumb_dir()
    if d is None:
        return
    try:
        for n in os.listdir(d):
            m = _THUMB_RE.match(n)
            if m and m.group("name") == name:
                try:
                    os.unlink(os.path.join(d, n))
                except OSError:
                    pass
    except OSError:
        pass


def _make_thumb(src, dst, is_video):
    """ffmpeg -> JPEG, at most THUMB_MAX_PX on the longer side. True on success."""
    ff = video_convert.FFMPEG
    if not ff:
        return False
    vf = ("scale=w='min(%d\\,iw)':h='min(%d\\,ih)'"
          ":force_original_aspect_ratio=decrease" % (THUMB_MAX_PX, THUMB_MAX_PX))
    for ss in (("1", "0") if is_video else (None,)):
        cmd = [ff, "-nostdin", "-v", "error", "-y"]
        if ss is not None:
            cmd += ["-ss", ss]
        cmd += ["-i", src, "-frames:v", "1", "-vf", vf, "-q:v", "5", "-f", "mjpeg", dst]
        try:
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                               timeout=THUMB_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError):
            return False
        if r.returncode == 0 and os.path.isfile(dst) and os.path.getsize(dst) > 0:
            return True
    return False


def get_thumb(name):
    """Path to the JPEG preview (generated lazily), or None."""
    if not isinstance(name, str) or name != safe(name):
        return None
    src = media_path(name)
    kd = kind(name)
    if src is None or kd is None or not os.path.isfile(src):
        return None
    d = _thumb_dir()
    if d is None:
        return None
    try:
        dst = os.path.join(d, _thumb_key(name, src))
    except OSError:
        return None
    if os.path.isfile(dst):
        return dst
    if not video_convert.FFMPEG:
        return None
    with _thumb_slots:
        if os.path.isfile(dst):
            return dst
        tmp = None
        try:
            os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".jpg", dir=d)
            os.close(fd)
            if not _make_thumb(src, tmp, kd == "video"):
                return None
            _remove_thumbs(name)                 # old versions (different mtime/size)
            os.replace(tmp, dst)
            tmp = None
            return dst
        except OSError:
            return None
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass


def cleanup_stale_thumbs():
    """At startup delete previews of files that no longer exist (or have changed)."""
    d = _thumb_dir()
    removed = 0
    try:
        names = os.listdir(d) if d else []
    except OSError:
        return 0
    for n in names:
        p = os.path.join(d, n)
        m = _THUMB_RE.match(n)
        keep = False
        if m:
            src = media_path(m.group("name"))
            try:
                keep = bool(src) and os.path.isfile(src) and _thumb_key(m.group("name"), src) == n
            except OSError:
                keep = False
        if not keep:
            try:
                if os.path.isfile(p) and not os.path.islink(p):
                    os.unlink(p)
                    removed += 1
            except OSError:
                pass
    return removed



# ---------- kiosk update ----------
_kiosk_cache = {"key": None, "release": None}
_kiosk_lock = threading.Lock()                # release cache only; never together with _lock
# Screens with a freshly requested update that have not polled since.
# The box keeps sending its last result (declined/failed/no_permission) on
# every poll; the first poll after a new request must therefore not treat it
# as an answer to it, otherwise a repeated request would vanish before the
# box learns about it. Under _lock.
_kiosk_fresh = set()


def kiosk_release():
    """Valid kiosk release {versionCode, versionName, sha256, size}, else None.

    Reads KIOSK_DIR/kiosk.json and checks that `size` matches the real APK.
    The result is cached by mtime and size of both files (a poll every
    minute from every box must not parse the JSON each time). Call outside _lock.
    """
    jpath = os.path.join(KIOSK_DIR, KIOSK_JSON)
    apath = os.path.join(KIOSK_DIR, KIOSK_APK)
    try:
        js, ast = os.stat(jpath), os.stat(apath)
    except OSError:
        return None
    if not stat.S_ISREG(js.st_mode) or not stat.S_ISREG(ast.st_mode):
        return None
    key = (jpath, js.st_mtime_ns, js.st_size, ast.st_mtime_ns, ast.st_size)
    with _kiosk_lock:
        if _kiosk_cache["key"] == key:
            rel = _kiosk_cache["release"]
            return dict(rel) if rel else None
    rel = None
    if js.st_size <= KIOSK_JSON_MAX:
        try:
            with open(jpath, encoding="utf-8") as fh:
                rel = validate_kiosk_release(json.load(fh))
        except (OSError, ValueError):            # includes JSONDecodeError and UnicodeDecodeError
            rel = None
    if rel and rel["size"] != ast.st_size:
        rel = None
    with _kiosk_lock:
        _kiosk_cache["key"], _kiosk_cache["release"] = key, rel
    return dict(rel) if rel else None


def _kiosk_int(raw):
    """versionCode from the query (digits only, int > 0), else None."""
    if not isinstance(raw, str) or not re.fullmatch(r"[0-9]{1,10}", raw):
        return None
    v = int(raw)
    return v if 0 < v <= 2 ** 31 - 1 else None


def _kiosk_flag(d):
    """Requested kiosk update for the screen (versionCode), or None."""
    v = d.get("kiosk_update") if isinstance(d, dict) else None
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None


def kiosk_poll(dev, have, state, health=None):
    """Kiosk app poll: records its state (and the box health) in _online
    and possibly resolves the update request. Returns True while the request
    is still pending.

    An unknown screen is ignored (nothing is written). State goes to disk only
    when the flag is actually cleared, not on every poll; box health stays in
    memory only, never on disk.
    """
    with _lock:
        devices = _state.get("devices")
        d = devices.get(dev) if isinstance(devices, dict) else None
        if not isinstance(d, dict):
            return False
        entry = _online.setdefault(dev, {})
        if have is not None:
            entry["kiosk_code"] = have
        if state is not None:
            entry["kiosk_state"] = state
        if health:
            kiosk_health_update(entry, health, int(time.time()))
        flag = _kiosk_flag(d)
        if flag is None:
            return False
        fresh = dev in _kiosk_fresh
        _kiosk_fresh.discard(dev)
        if not ((have is not None and have >= flag)
                or (state in KIOSK_RESOLVING and not fresh)):
            return True
        del d["kiosk_update"]
        try:
            save()
        except Exception as exc:
            # a public GET must not crash; the request is resolved in memory
            # (after a restart the box would ask again - harmless)
            sys.stderr.write("  kiosk_poll: save failed (%s)\n" % type(exc).__name__)
        return False


# box health fields that do not come straight from the poll snapshot (counters, times)
_KIOSK_EXTRA = ("kiosk_seen", "kiosk_boots", "kiosk_last_boot", "kiosk_ram_low_polls")


def touch_device(dev, headers, render=None):
    """Record a heartbeat. Memory under _lock (briefly), file I/O outside it."""
    now = int(time.time())
    with _lock:
        must_log = _touch_memory(dev, headers, render, now)
    if must_log:
        _log_heartbeat(dev, now)


def _touch_memory(dev, headers, render, now):
    """Update _online; return True when the heartbeat should be written to telemetry."""
    old = _online.get(dev, {})
    entry = {"last_seen": now, "agent": headers.get("User-Agent", "")[:240],
             "last_logged": int(old.get("last_logged", 0)),
             "screen": old.get("screen"),
             "last_ok": int(old.get("last_ok", 0)),
             "err": int(old.get("err", 0)),
             "why": old.get("why", ""),
             "h264": old.get("h264"),
             "quota": old.get("quota", ""),
             "cache": old.get("cache", ""),
             "drop": old.get("drop", ""),
             "stall": int(old.get("stall", 0)),
             "kiosk": old.get("kiosk", ""),
             # reported by the kiosk app via /api/kiosk/release, not by the player
             "kiosk_code": old.get("kiosk_code"),
             "kiosk_state": old.get("kiosk_state", "")}
    # box health is also reported by the kiosk (not the player); a heartbeat must not drop it
    for k in KIOSK_HEALTH_FIELDS + _KIOSK_EXTRA:
        if k in old:
            entry[k] = old[k]
    if render:
        if render.get("screen"):
            entry["screen"] = render["screen"]
        if render.get("h264") is not None:
            entry["h264"] = render["h264"]
        if render.get("quota"):
            entry["quota"] = render["quota"]
        if render.get("kiosk"):
            entry["kiosk"] = render["kiosk"]
        if render.get("cache"):
            entry["cache"] = render["cache"]
        if render.get("drop"):
            entry["drop"] = render["drop"]
        if render.get("stall"):
            entry["stall"] = int(render["stall"])
        entry["err"] = int(render.get("err") or 0)
        entry["why"] = render.get("why", "") if entry["err"] else ""
        if render.get("ok"):
            entry["last_ok"] = now
    _online[dev] = entry
    if now - entry["last_logged"] < 60:
        return False
    entry["last_logged"] = now
    return True


def _log_heartbeat(dev, now):
    """Telemetry to disk under its own small lock - never under the global _lock."""
    event = {"at": now, "device": dev}
    with _telemetry_lock:
        prune_telemetry(now)
        try:
            with open(TELEMETRY, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError:
            pass




def send_telegram(text):
    """Send a message via the Telegram bot. Returns False on failure (retry next time)."""
    import urllib.request
    url = f"https://api.telegram.org/bot{ALERT_TG_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": ALERT_TG_CHAT, "text": text}).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=10) as resp:
            return resp.status == 200
    except Exception as exc:
        # the exception text may contain a URL with the bot token - never log it
        err = str(exc)
        if ALERT_TG_TOKEN:
            err = err.replace(ALERT_TG_TOKEN, "***")
        sys.stderr.write(f"alert: telegram failed: {err}\n")  # Czech text asserted by test_telegram, kept
        return False


_alerted = set()


def alert_loop():
    """Watch online/offline transitions and send notifications (runs as a thread)."""
    global _alerted
    while True:
        time.sleep(ALERT_CHECK_SECONDS)
        with _lock:
            online = {dev: dict(info) for dev, info in _online.items()}
        events, alerted = alert_events(online, _alerted, int(time.time()),
                                       ALERT_OFFLINE_MIN * 60)
        # the state advances only when everything was sent; otherwise the events
        # are recomputed and sent again in the next round
        if all(send_telegram(format_alert(event)) for event in events):
            _alerted = alerted


def prune_telemetry(now=None):
    """Trim the history once a day; never reads or writes more than the limit."""
    global _telemetry_pruned_at, _availability_cache
    now = int(now or time.time())
    if now - _telemetry_pruned_at < 24 * 60 * 60:
        return
    _telemetry_pruned_at = now
    try:
        size = os.path.getsize(TELEMETRY)
    except OSError:
        return
    cutoff = now - TELEMETRY_RETENTION_DAYS * 24 * 60 * 60
    start = max(0, size - TELEMETRY_MAX_BYTES)
    keep = []
    try:
        with open(TELEMETRY, "r", encoding="utf-8") as fh:
            if start:
                fh.seek(start)
                fh.readline()  # skip a possibly split JSON line at the start
            for line in fh:
                try:
                    event = json.loads(line)
                    if int(event.get("at", 0)) >= cutoff:
                        keep.append(json.dumps(event, ensure_ascii=False))
                except (ValueError, TypeError):
                    pass
        if start or len(keep) * 2 < size:
            tmp = TELEMETRY + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write("\n".join(keep) + ("\n" if keep else ""))
            os.replace(tmp, TELEMETRY)
            _availability_cache = {}
    except OSError:
        pass


def availability_report(dev, hours):
    """Compress the JSONL heartbeats into buckets; gaps are not the TV's physical state."""
    global _availability_cache
    now = int(time.time())
    bucket = 3600 if hours <= 24 else 3 * 3600
    start = now - hours * 3600
    try:
        tstat = os.stat(TELEMETRY)
        signature = (tstat.st_mtime_ns, tstat.st_size)
    except OSError:
        signature = (0, 0)
    key = (dev, hours, signature)
    cached = _availability_cache.get(key)
    if cached and now - cached[0] < 60:
        return cached[1]

    slots = [{"at": start + index * bucket, "state": "unknown"}
             for index in range((hours * 3600 + bucket - 1) // bucket)]
    seen = set()
    stamps = []
    truncated = signature[1] > TELEMETRY_MAX_BYTES
    try:
        with open(TELEMETRY, "r", encoding="utf-8") as fh:
            if truncated:
                fh.seek(-TELEMETRY_MAX_BYTES, os.SEEK_END)
                fh.readline()
            for line in fh:
                try:
                    event = json.loads(line)
                    stamp = int(event.get("at", 0))
                except (ValueError, TypeError):
                    continue
                if event.get("device") != dev or stamp < start or stamp > now:
                    continue
                stamps.append(stamp)
                index = min(len(slots) - 1, (stamp - start) // bucket)
                seen.add(index)
    except OSError:
        pass

    first_seen, last_seen = (min(stamps), max(stamps)) if stamps else (0, 0)
    for index, slot in enumerate(slots):
        if index in seen:
            slot["state"] = "seen"
        elif (first_seen and start + index * bucket >= first_seen
              and start + (index + 1) * bucket <= last_seen):
            slot["state"] = "gap"
    known = sum(slot["state"] != "unknown" for slot in slots)
    report = {"device": dev, "from": start, "to": now, "bucket_seconds": bucket,
              "first_seen": first_seen, "last_seen": last_seen, "events": len(stamps),
              "coverage_pct": round(100 * len(seen) / known) if known else None,
              "buckets": slots, "truncated": truncated}
    _availability_cache = {key: (now, report)}
    return report



class _CountingReader:
    """Wrapper over rfile that counts how many bytes the parser actually read."""

    def __init__(self, fp):
        self._fp = fp
        self.count = 0

    def read(self, n=-1):
        chunk = self._fp.read(n)
        self.count += len(chunk)
        return chunk


class _Server(ThreadingHTTPServer):
    """Larger backlog of waiting connections (the default of 5 resets connections when
    many clients arrive at once: after a power outage all screens report in together,
    and the admin uploads several files)."""
    request_queue_size = 128


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Signage/1.0"
    # The timeout applies to each individual socket operation (not the whole
    # request), so a long upload that keeps flowing survives; slowloris does not.
    timeout = 60
    # Headers and body go out in two writes; with Nagle and the client's delayed
    # ACK this added ~41 ms to every further request on a keep-alive connection.
    disable_nagle_algorithm = True

    def log_message(self, *a):
        sys.stderr.write("%s - %s\n" % (self.client_address[0], a[0] % a[1:]))

    def _client_ip(self):
        if TRUST_PROXY:
            xff = self.headers.get("X-Forwarded-For", "")
            last = xff.rsplit(",", 1)[-1].strip()
            if last:
                return last
        return self.client_address[0]

    def _lang(self):
        """Response language (None = original text): ?lang= before Accept-Language."""
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return pick_lang(query.get("lang", [""])[0], self.headers.get("Accept-Language"))

    def _lang_header(self):
        return {"Content-Language": self._lang() or "en"}

    def _err_body(self, code, **params):
        return {"error": error_text(code, self._lang(), **params), "code": code}

    def _err(self, status, code, extra=None, **params):
        """JSON error {error, code} + Content-Language."""
        self._json(self._err_body(code, **params), status, extra)

    def _err_from(self, status, err):
        """Error from validate_* (ErrText carries code and params)."""
        self._err(status, err.code, **err.params)

    def _upload_err_payload(self, code, **params):
        return {"ok": False, "saved": [],
                "errors": [error_text(code, self._lang(), **params)],
                "error_codes": [code], "code": code}

    def _too_many(self, retry, code):
        self.close_connection = True
        self._send(429, "application/json; charset=utf-8",
                   json.dumps(self._err_body(code)),
                   {"Retry-After": str(retry), "Connection": "close",
                    "Cache-Control": "no-store", **self._lang_header()})

    def _auth_ok(self):
        p = urllib.parse.urlparse(self.path).path
        # "/" serves admin.html, so it is protected like /admin
        # (it cannot go into PROTECTED: the "/" prefix would lock everything)
        if p != "/" and not any(p == x or p.startswith(x) for x in PROTECTED):
            return True
        ip = self._client_ip()
        retry = _login_limiter.retry_after(ip)
        if retry:
            self._too_many(retry, "too_many_attempts")
            return False
        hdr = self.headers.get("Authorization", "")
        if hdr.startswith("Basic "):
            try:
                user, _, pw = base64.b64decode(hdr[6:]).decode("utf-8").partition(":")
                # both comparisons are always evaluated (no short-circuit) in constant time
                user_ok = hmac.compare_digest(user.encode("utf-8"), ADMIN_USER.encode("utf-8"))
                pass_ok = hmac.compare_digest(pw.encode("utf-8"), ADMIN_PASS.encode("utf-8"))
                if ADMIN_USER and ADMIN_PASS and user_ok and pass_ok:
                    return True
            except (ValueError, UnicodeDecodeError):
                pass
        if hdr:
            _login_limiter.hit(ip)
        self.close_connection = True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Signage admin"')
        self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive")
        body = json.dumps(self._err_body("unauthorized"), ensure_ascii=False).encode("utf-8")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Language", self._lang() or "en")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        return False

    # ---------- helpers ----------
    def _idem_settle(self):
        """Releases/stores the Idempotency-Key reservation (once). Called BEFORE the reply is
        written: otherwise a client that retries immediately after a failed upload could
        still see the key reserved (409) because the handler's finally has not run yet."""
        pending = getattr(self, "_idem_pending", None)
        if pending:
            self._idem_pending = None
            _idem_finish(pending[0], pending[1], getattr(self, "_upload_result", None))

    def _send(self, code, ctype, body, extra=None):
        self._idem_settle()
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive")
        headers = dict(extra or {})
        headers.update(_cors_headers(self.headers.get("Origin", "")))
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200, extra=None):
        headers = {"Cache-Control": "no-store"}
        if isinstance(obj, dict) and ("code" in obj and "error" in obj or "error_codes" in obj):
            headers.update(self._lang_header())         # error response
        headers.update(extra or {})
        self._send(code, "application/json; charset=utf-8",
                   json.dumps(obj, ensure_ascii=False), headers)

    def _file(self, path, ctype=None):
        # open first, take the size from fstat on the open file: between getsize
        # and open the file could vanish or change
        try:
            fh = open(path, "rb")
        except OSError:                          # FileNotFoundError, directory, permissions
            self._send(404, "text/plain", "not found")
            return
        with fh:
            try:
                st = os.fstat(fh.fileno())
            except OSError:
                self._send(404, "text/plain", "not found")
                return
            if not stat.S_ISREG(st.st_mode):
                self._send(404, "text/plain", "not found")
                return
            self._file_open(fh, st.st_size, path, ctype)

    def _file_open(self, fh, size, path, ctype, cache=None):
        ctype = ctype or mimetypes.guess_type(path)[0] or "application/octet-stream"
        if path.endswith((".mov", ".m4v")):
            ctype = "video/mp4"       # TVs reject video/quicktime in <video>
        if path.endswith(".webmanifest"):
            ctype = "application/manifest+json"
        bounds = _range_bounds(self.headers.get("Range"), size)
        if bounds is None:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            for k, v in _cors_headers(self.headers.get("Origin", "")).items():
                self.send_header(k, v)
            self.end_headers()
            return
        start, end, partial = bounds
        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive")
        for k, v in _cors_headers(self.headers.get("Origin", "")).items():
            self.send_header(k, v)
        # never cache HTML (otherwise the browser keeps an old admin/player version),
        # media can happily be cached
        if cache:
            self.send_header("Cache-Control", cache)
        elif ctype.startswith("text/html"):
            self.send_header("Cache-Control", "no-store, must-revalidate")
        else:
            self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        if self.command == "HEAD":
            return
        fh.seek(start)
        left = length
        while left > 0:
            chunk = fh.read(min(65536, left))
            if not chunk:
                # the file was truncated mid-flight: the promised length can no longer
                # be delivered, close the connection so the client does not hang
                self.close_connection = True
                return
            try:
                self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
                return
            left -= len(chunk)

    def _reject_and_close(self, status, code):
        self.close_connection = True
        self._send(status, "application/json; charset=utf-8",
                   json.dumps(self._err_body(code)),
                   {"Connection": "close", **self._lang_header()})

    def _body(self):
        """Body of a JSON endpoint (max MAX_JSON_BODY). On error responds and returns None."""
        raw = self.headers.get("Content-Length") or "0"
        try:
            n = int(raw)
        except ValueError:
            n = -1
        if n < 0:
            self._reject_and_close(400, "invalid_content_length")
            return None
        if n > MAX_JSON_BODY:
            self._reject_and_close(413, "request_too_large")
            return None
        data = b""
        while len(data) < n:
            chunk = self.rfile.read(min(65536, n - len(data)))
            if not chunk:
                break
            data += chunk
        return data

    # ---------- routing ----------
    def do_HEAD(self):
        if self._auth_ok():
            self.route()
    def do_GET(self):
        if self._auth_ok():
            self.route()

    def do_POST(self):
        if not self._auth_ok():
            return
        p = urllib.parse.urlparse(self.path).path
        if p == "/api/upload":
            return self.upload()
        raw = self._body()
        if raw is None:
            return
        try:
            req = json.loads(raw.decode("utf-8"))
        except ValueError:                       # includes UnicodeDecodeError and an empty body
            return self._err(400, "invalid_json")
        if not isinstance(req, dict):
            return self._err(400, "body_not_object")
        if p == "/api/order":
            return self.reorder(req)
        if p == "/api/delete":
            return self.delete(req)
        if p == "/api/item":
            return self.edit_item(req)
        if p == "/api/settings":
            return self.set_settings(req)
        if p == "/api/screens":
            return self.create_screen(req)
        if p == "/api/pair/request":
            return self.pair_request(req)
        if p == "/api/pair/claim":
            return self.pair_claim(req)
        m = re.match(r"^/api/screens/([\w-]+)/kiosk-update$", p)
        if m:
            return self.kiosk_update(m.group(1), req)
        self._err(404, "unknown_endpoint")

    def route(self):
        p = urllib.parse.urlparse(self.path).path
        if p == "/robots.txt":
            return self._send(200, "text/plain", "User-agent: *\nDisallow: /\n")
        if p == "/qr.png":
            return self.qr_png()
        if p == "/pair-qr.png":
            return self.pair_qr()
        if p == "/sw.js":
            return self._file(os.path.join(TVDIR, "sw.js"), "application/javascript; charset=utf-8")
        if p in ("/connect", "/connect/"):
            return self._file(os.path.join(TVDIR, "connect.html"), "text/html; charset=utf-8")
        if p in ("/", "/admin", "/admin/"):
            return self._file(os.path.join(TVDIR, "admin.html"), "text/html; charset=utf-8")
        # /tv, /tv/ and /tv/<name> -> player (no name = default device)
        if p in ("/tv", "/tv/") or re.match(r"^/tv/([\w-]+)/?$", p):
            return self._file(os.path.join(TVDIR, "player.html"), "text/html; charset=utf-8")
        m = re.match(r"^/api/tv/([\w-]+)/playlist$", p)
        if m:
            if not valid_device(m.group(1)):
                return self._err(404, "invalid_device")
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            return self.playlist(m.group(1), query.get("heartbeat") == ["1"], query)
        if p == "/api/health":
            return self.health()
        if p == "/api/devices":
            return self.list_devices()
        m = re.match(r"^/api/screens/([\w-]+)/qr\.png$", p)
        if m:
            return self.screen_qr(m.group(1))
        m = re.match(r"^/api/devices/([\w-]+)/availability$", p)
        if m:
            return self.availability(m.group(1))
        if p == "/api/pair/status":
            return self.pair_status()
        # kiosk update: public (the box has no password), outside PROTECTED
        if p == "/api/kiosk/release":
            return self.kiosk_release_api()
        if p == KIOSK_URL:
            return self.kiosk_apk()
        if p.startswith("/api/thumb/"):
            path = get_thumb(urllib.parse.unquote(p[len("/api/thumb/"):]))
            if path is None:
                return self._err(404, "preview_unavailable")
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
            except OSError:
                return self._err(404, "preview_unavailable")
            return self._send(200, "image/jpeg", data,
                              {"Cache-Control": "private, max-age=300"})
        if p.startswith("/media/"):
            name = safe(urllib.parse.unquote(p[len("/media/"):]))
            path = media_path(name)
            if path is None:
                return self._send(404, "text/plain", "not found")
            return self._file(path)
        if p.startswith("/assets/"):
            return self._file(asset_path(p))
        self._send(404, "text/plain", "not found")

    # ---------- TV pairing ----------
    def pair_request(self, req):
        retry = _pair_limiter.acquire(self._client_ip())
        if retry:
            return self._too_many(retry, "too_many_requests")
        client = str(req.get("client", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", client):
            return self._err(400, "invalid_install_id")
        with _lock:
            prune_pairs()
            pairs = _state["pairs"]
            for code, pair in pairs.items():
                if pair.get("client") == client:
                    return self._json({"code": code, "expires_in": max(0, pair["expires"] - int(time.time()))})
            if len(pairs) >= 200:
                return self._err(429, "pair_capacity_full")
            code = ""
            while not code or code in pairs:
                code = "".join(secrets.choice(PAIR_CODE_CHARS) for _ in range(6))
            pairs[code] = {"client": client, "expires": int(time.time()) + PAIR_TTL_SECONDS}
            save()
        self._json({"code": code, "expires_in": PAIR_TTL_SECONDS})

    def pair_status(self):
        client = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("client", [""])[0]
        with _lock:
            prune_pairs()
            for code, pair in _state["pairs"].items():
                if pair.get("client") != client:
                    continue
                if pair.get("device"):
                    device_name = pair["device"]
                    del _state["pairs"][code]
                    try:
                        save()
                    except Exception as exc:
                        # a public GET must not crash; pairing is already done, only
                        # writing the code deletion failed (it expires on its own)
                        sys.stderr.write("  pair_status: save failed (%s)\n" % type(exc).__name__)
                    return self._json({"status": "paired", "device": device_name})
                return self._json({"status": "pending", "expires_in": max(0, pair["expires"] - int(time.time()))})
        self._json({"status": "missing"}, 404)

    def pair_claim(self, req):
        code = str(req.get("code", "")).upper().strip()
        device_name = str(req.get("device", "") or "").strip()
        new_label = None
        if not device_name:
            # no name = new screen with a random name (optional label)
            new_label, err = validate_label(req.get("label"))
            if err:
                return self._err_from(400, err)
        if not re.fullmatch(r"[A-Z2-9]{6}", code) or (
                new_label is None and not valid_device(device_name)):
            return self._err(400, "invalid_pair_input")
        with _lock:
            prune_pairs()
            pair = _state["pairs"].get(code)
            if not pair:
                return self._err(404, "pair_code_expired")
            if pair.get("device"):
                return self._err(409, "pair_code_used")
            if new_label is not None:
                device_name = new_screen_name(new_label)
                device(device_name)
                if new_label:
                    _state["devices"][device_name].setdefault("settings", {})["label"] = new_label
            else:
                device(device_name)
            pair["device"] = device_name
            pair["expires"] = int(time.time()) + 120
            save()
        self._json({"ok": True, "device": device_name})

    def create_screen(self, req):
        """Protected endpoint: new screen with a random name and a label."""
        label, err = validate_label(req.get("label"))
        if err:
            return self._err_from(400, err)
        with _lock:
            devices = _state.setdefault("devices", {})
            name = new_screen_name(label, devices)
            d = device(name)
            if label:
                d.setdefault("settings", {})["label"] = label
            try:
                save()
            except Exception as exc:
                devices.pop(name, None)
                sys.stderr.write("  state save failed (%s)\n" % type(exc).__name__)
                code = 507 if getattr(exc, "errno", None) == errno.ENOSPC else 500
            else:
                return self._json({"ok": True, "device": name, "label": label})
        self._err(code, "state_save_failed")

    def screen_qr(self, dev):
        """QR with the screen's player address (protected; the address is the 'password' to the media)."""
        if not valid_device(dev):
            return self._send(404, "text/plain", "not found")
        host = self.headers.get("Host", "")
        m = re.fullmatch(r"[A-Za-z0-9.-]{1,253}(?::([0-9]{1,5}))?", host)
        if not m or (m.group(1) and int(m.group(1)) > 65535):
            return self._send(400, "text/plain", "invalid host")
        proto = self.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower() or "http"
        if proto not in ("http", "https"):
            return self._send(400, "text/plain", "invalid proto")
        base = os.environ.get("SIGNAGE_PUBLIC_URL", "").rstrip("/") or (proto + "://" + host)
        target = base + ("/tv/" if dev == "default" else "/tv/" + urllib.parse.quote(dev))
        if segno is None:
            return self._send(503, "text/plain", "QR generator is unavailable")
        png = _qr_cache_get(target)
        if png is None:
            import io
            buf = io.BytesIO()
            segno.make(target, error="h").save(buf, kind="png", scale=8, border=2,
                                               dark="#101010", light="#f5f0e4")
            png = buf.getvalue()
            _qr_cache_put(target, png)
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(png)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(png)

    def pair_qr(self):
        code = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("code", [""])[0].upper()
        with _lock:
            prune_pairs()
            if code not in _state["pairs"]:
                return self._send(404, "text/plain", "pairing code not found")
        host = self.headers.get("Host", "")
        m = re.fullmatch(r"[A-Za-z0-9.-]{1,253}(?::([0-9]{1,5}))?", host)
        if not m or (m.group(1) and int(m.group(1)) > 65535):
            return self._send(400, "text/plain", "invalid host")
        proto = self.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower() or "http"
        if proto not in ("http", "https"):
            return self._send(400, "text/plain", "invalid proto")
        base = os.environ.get("SIGNAGE_PUBLIC_URL", "").rstrip("/") or (proto + "://" + host)
        target = base + "/admin?pair=" + urllib.parse.quote(code)
        if segno is None:
            return self._send(503, "text/plain", "QR generator is unavailable")
        png = _qr_cache_get(target)
        if png is None:
            import io
            buf = io.BytesIO()
            segno.make(target, error="h").save(buf, kind="png", scale=12, border=2, dark="#101010", light="#f5f0e4")
            png = buf.getvalue()
            _qr_cache_put(target, png)
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(png)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive")
        for k, v in _cors_headers(self.headers.get("Origin", "")).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(png)


    # ---------- API ----------
    def playlist(self, dev, heartbeat=False, query=None):
        # File I/O (isfile, telemetry) and writing the response are outside the
        # global _lock: a slow disk or client must not block other requests.
        with _lock:
            # Public endpoint: we do not create unknown devices nor write
            # telemetry about them (devices arise via upload/pairing/admin).
            known = isinstance(_state.get("devices"), dict) and dev in _state["devices"]
            raw = _state["devices"][dev].get("items") if known and isinstance(
                _state["devices"][dev], dict) else None
            files = [i.get("file") for i in raw if isinstance(i, dict)] \
                if isinstance(raw, list) else []
        present = {f: os.path.isfile(media_path(f) or "") for f in files}
        with _lock:
            # a file added in the meantime is not in `present` - treat it as existing
            d = (device(dev, lambda f: present.get(f, True))
                 if known else {"version": 0, "items": []})
            items = [{"url": f"/media/{i['file']}", "type": i["type"],
                      "seconds": i.get("seconds", 10),
                      "sound": bool(i.get("sound", False)),
                      "enabled": bool(i.get("enabled", True)),
                      "from": i.get("from", ""), "until": i.get("until", "")}
                     for i in d["items"]]
            for out, i in zip(items, d["items"]):
                if i.get("schedule"):
                    out["schedule"] = copy.deepcopy(i["schedule"])
            body = {"version": d["version"], "device": dev, "items": items,
                    # server build: the player uses it to notice a new version was
                    # deployed and reloads itself - otherwise the screen would run
                    # old code until a manual restart
                    "build": APP_VERSION,
                    "settings": playlist_settings(d.get("settings", {}))}
        if heartbeat and known:
            touch_device(dev, self.headers, _render_report(query))
        self._json(body)

    def list_devices(self):
        rel = kiosk_release()                  # file I/O outside _lock
        with _lock:
            out = {}
            now = int(time.time())
            for k, v in _state["devices"].items():
                b = sum(_file_size(i["file"]) for i in v.get("items", []))
                seen = _online.get(k, {})
                last_seen = int(seen.get("last_seen", 0))
                lab = (v.get("settings") or {}).get("label")
                out[k] = {"version": v["version"], "count": len(v["items"]),
                          "label": lab if isinstance(lab, str) else "",
                          "size_mb": round(b / (1024 * 1024), 1),
                          "online": bool(last_seen and now - last_seen <= 45),
                          "last_seen": last_seen, "agent": seen.get("agent", ""),
                          "screen": seen.get("screen"),
                          "render_err": int(seen.get("err", 0)),
                          "render_why": seen.get("why", ""),
                          "h264": seen.get("h264"),
                          "storage_mb": seen.get("quota", ""),
                          "kiosk": seen.get("kiosk", ""),
                          "cached_items": seen.get("cache", ""),
                          "snapshots": seen.get("drop", ""),
                          "stalls": int(seen.get("stall", 0)),
                          "blank": is_blank(seen, now),
                          # kiosk update: version and state are reported by the box, the flag by the admin
                          "kiosk_code": seen.get("kiosk_code"),
                          "kiosk_state": seen.get("kiosk_state", "") or "",
                          "kiosk_update": _kiosk_flag(v),
                          # box health from kiosk polls (memory only; null = unknown)
                          "kiosk_ram": seen.get("kiosk_ram"),
                          "kiosk_ram_low": seen.get("kiosk_ram_low"),
                          "kiosk_ram_total": seen.get("kiosk_ram_total"),
                          "kiosk_up": seen.get("kiosk_up"),
                          "kiosk_boots": int(seen.get("kiosk_boots") or 0),
                          "kiosk_last_boot": seen.get("kiosk_last_boot"),
                          "kiosk_rssi": seen.get("kiosk_rssi"),
                          "kiosk_net": seen.get("kiosk_net") or "",
                          "kiosk_crash": seen.get("kiosk_crash"),
                          "kiosk_seen": seen.get("kiosk_seen")}
        self._json({"devices": out,
                    "kiosk_release": ({"versionCode": rel["versionCode"],
                                       "versionName": rel["versionName"]} if rel else None)})

    # ---------- kiosk update ----------
    def kiosk_release_api(self):
        """Public poll of the kiosk app: current release + whether the admin requested it."""
        retry = _kiosk_limiter.acquire(self._client_ip())
        if retry:
            return self._too_many(retry, "too_many_requests")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        dev = query.get("device", [""])[0]
        have = _kiosk_int(query.get("have", [""])[0])
        state = query.get("state", [""])[0]
        state = state if state in KIOSK_STATES else None   # invalid is ignored, not a 400
        rel = kiosk_release()                              # file I/O outside _lock
        health = _kiosk_health(query)                      # invalid = None, not a 400
        requested = kiosk_poll(dev, have, state, health) if valid_device(dev) else False
        if rel is None:
            return self._err(404, "no_kiosk_release")
        self._json({"versionCode": rel["versionCode"], "versionName": rel["versionName"],
                    "sha256": rel["sha256"], "size": rel["size"], "url": KIOSK_URL,
                    "requested": requested})

    def kiosk_apk(self):
        """APK of the current release (only when the release is valid and the size matches)."""
        rel = kiosk_release()
        if rel is None:
            return self._send(404, "text/plain", "not found")
        path = os.path.join(KIOSK_DIR, KIOSK_APK)
        try:
            fh = open(path, "rb")
        except OSError:
            return self._send(404, "text/plain", "not found")
        with fh:
            try:
                st = os.fstat(fh.fileno())
            except OSError:
                return self._send(404, "text/plain", "not found")
            # file swapped between the check and open: better a 404 than foreign bytes
            if not stat.S_ISREG(st.st_mode) or st.st_size != rel["size"]:
                return self._send(404, "text/plain", "not found")
            self._file_open(fh, st.st_size, path, KIOSK_MIME, cache="no-cache")

    def kiosk_update(self, dev, req):
        """Protected endpoint: requests (or, with {"cancel": true}, cancels) a kiosk update."""
        cancel = req.get("cancel") is True
        rel = None if cancel else kiosk_release()          # file I/O outside _lock
        with _lock:
            devices = _state.get("devices")
            d = devices.get(dev) if valid_device(dev) and isinstance(devices, dict) else None
            if not isinstance(d, dict):
                err = (404, "invalid_device")
            elif cancel:
                err = None
                old = d.pop("kiosk_update", None)
                requested = None
            elif rel is None:
                err = (409, "no_kiosk_release")
            else:
                code = (_online.get(dev) or {}).get("kiosk_code")
                if isinstance(code, int) and code >= rel["versionCode"]:
                    err = (409, "kiosk_up_to_date")
                else:
                    err = None
                    old = d.get("kiosk_update")
                    requested = d["kiosk_update"] = rel["versionCode"]
            if err is None:
                if requested is None:
                    _kiosk_fresh.discard(dev)
                else:
                    _kiosk_fresh.add(dev)
            if err is None and old != requested:
                try:
                    save()
                except Exception as exc:
                    _kiosk_fresh.discard(dev)
                    if old is None:
                        d.pop("kiosk_update", None)
                    else:
                        d["kiosk_update"] = old
                    sys.stderr.write("  state save failed (%s)\n" % type(exc).__name__)
                    err = (507 if getattr(exc, "errno", None) == errno.ENOSPC else 500,
                           "state_save_failed")
        if err:
            return self._err(*err)
        self._json({"ok": True, "requested": requested})

    def availability(self, dev):
        if not valid_device(dev):
            return self._err(400, "invalid_device")
        raw = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("hours", ["24"])[0]
        try:
            hours = int(raw)
        except ValueError:
            return self._err(400, "hours_not_integer")
        if not 1 <= hours <= AVAILABILITY_MAX_HOURS:
            return self._err(400, "hours_out_of_range", max=AVAILABILITY_MAX_HOURS)
        report = availability_report(dev, hours)       # reads a file -> outside _lock
        with _lock:
            last_live = int((_online.get(dev) or {}).get("last_seen", 0))
        report = dict(report)          # the cache holds the original object
        report["online"] = bool(last_live and int(time.time()) - last_live <= 45)
        report["live_last_seen"] = last_live
        self._json(report)

    def health(self):
        # public endpoint without auth (/api/health is not in PROTECTED) - monitoring/demo
        with _lock:
            devs = _state.get("devices", {})
            ndev = len(devs)
            nitems = sum(len(v.get("items", [])) for v in devs.values())
        mb = round(storage_bytes() / (1024 * 1024), 1)
        now = int(time.time())
        blank = sorted(k for k, v in _online.items() if is_blank(v, now))
        recovery = bool(_state_recovery_required)
        self._json({"ok": not recovery, "state_ok": not recovery,
                    "state_error": ("state file cannot be read/backed up, writes are blocked"
                                    if recovery else ""),
                    "devices": ndev, "items": nitems, "storage_mb": mb,
                    "version": APP_VERSION,
                    "blank_screens": blank,
                    "alerts_enabled": bool(ALERT_TG_TOKEN and ALERT_TG_CHAT),
                    "autoconvert": video_convert.available(),
                    "max_file_mb": MAX_FILE_MB, "max_items": MAX_ITEMS,
                    "max_total_mb": MAX_TOTAL_MB})

    def _drain_body(self):
        """Bounded drain of an unused body (so the client sees the response, not a reset)."""
        try:
            left = min(int(self.headers.get("Content-Length") or 0), UPLOAD_DRAIN_MAX)
            while left > 0:
                chunk = self.rfile.read(min(65536, left))
                if not chunk:
                    break
                left -= len(chunk)
        except (ValueError, OSError):
            pass

    def _early_upload_reply(self, code, payload, extra):
        self.close_connection = True
        h = {"Connection": "close", "Cache-Control": "no-store"}
        if isinstance(payload, dict) and "error_codes" in payload:
            h.update(self._lang_header())
        h.update(extra)
        self._send(code, "application/json; charset=utf-8", json.dumps(payload), h)
        self._drain_body()

    def upload(self):
        retry = _upload_limiter.acquire(self._client_ip())
        if retry:
            # respond + bounded body drain so the client sees 429, not a connection reset
            return self._early_upload_reply(
                429, self._upload_err_payload("too_many_uploads"),
                {"Retry-After": str(retry)})
        dev = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get(
            "device", ["default"])[0]
        if not valid_device(dev):
            return self._early_upload_reply(
                400, self._upload_err_payload("invalid_device"), {})
        idem = self.headers.get("Idempotency-Key")
        if idem is not None:
            if not IDEM_KEY_RE.match(idem):
                return self._early_upload_reply(
                    400, self._upload_err_payload("invalid_idempotency_key"), {})
            state, stored = _idem_begin(dev, idem)
            if state == "replay":
                return self._early_upload_reply(
                    stored[0], stored[1], {"Idempotent-Replay": "true"})
            if state == "busy":
                return self._early_upload_reply(
                    409, self._upload_err_payload("upload_in_progress"),
                    {"Retry-After": "5"})
        self._upload_result = None
        self._idem_pending = (dev, idem) if idem is not None else None
        try:
            if not _upload_slots.acquire(blocking=False):
                # respond + bounded body drain (the client sees 503, not a connection
                # reset); the connection is closed after the reply to stay in sync
                return self._early_upload_reply(
                    503, self._upload_err_payload("server_busy"), {"Retry-After": "5"})
            try:
                return self._upload()
            finally:
                _upload_slots.release()
        finally:
            self._idem_settle()

    def _upload(self):
        ctype = self.headers.get("Content-Type", "")
        dev = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get(
            "device", ["default"])[0]
        # quick safeguard: an absurdly large batch is not even started to be read
        try:
            clen = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            clen = 0
        if clen > MAX_TOTAL_BYTES:
            return self._json(self._upload_err_payload("batch_too_large", max=MAX_TOTAL_MB), 413)
        try:
            src = _CountingReader(self.rfile)
            os.makedirs(MEDIA, exist_ok=True)
            parts = parse_multipart(src, ctype, clen, MAX_TOTAL_BYTES,
                                    max_file_bytes=MAX_FILE_BYTES, spool_dir=MEDIA)
        except FileTooLargeError:
            # the rest of the body is unread -> we close the connection after the
            # reply; beforehand we drain it (bounded, streamed, no RAM) so the
            # client sees 413 and not a "connection reset" mid-send
            self.close_connection = True
            self._send(
                413, "application/json; charset=utf-8",
                json.dumps(self._upload_err_payload("file_too_large", max=MAX_FILE_MB)),
                {"Connection": "close", "Cache-Control": "no-store", **self._lang_header()})
            left = min(max(clen - src.count, 0), UPLOAD_DRAIN_MAX)
            try:
                while left > 0:
                    chunk = self.rfile.read(min(65536, left))
                    if not chunk:
                        break
                    left -= len(chunk)
            except OSError:
                pass
            return
        except MultipartError:
            return self._json(self._upload_err_payload("upload_corrupt"), 400)
        except OSError as exc:
            if getattr(exc, "errno", None) != errno.ENOSPC:
                raise                       # dropped connection etc., as before
            self.close_connection = True    # disk full while streaming to disk
            return self._json(self._upload_err_payload("file_save_failed"), 507)
        try:
            payload = self._upload_files(parts, dev)
        finally:
            # temporary files that were not published (error, rejection, exception);
            # cleaned up BEFORE the client gets the reply
            for f in parts:
                f.discard()
        if payload["saved"]:
            self._upload_result = (200, payload)     # only a real success goes into idempotency
        elif self._upload_status:
            # nothing was saved due to load/space: 503 + Retry-After resp. 507
            code, extra = self._upload_status
            return self._json(payload, code, extra)
        self._json(payload)

    def _upload_files(self, parts, dev):
        done, errs, warns, fixes = [], [], [], []
        codes = []
        lang = self._lang()
        self._upload_status = None      # (http, headers) when the conversion failed and nothing was saved

        def add_err(fname, code, **params):
            errs.append(f"{fname}: {error_text(code, lang, **params)}")
            codes.append(code)

        for f in parts:
            if not f.filename:
                continue
            fname = f.filename
            # one bad file must not bring down the whole batch
            try:
                k = kind(fname)
                if not k:
                    add_err(fname, (um := unsupported_msg(fname)).code, **um.params)
                    continue
                with _lock:
                    if len(device(dev)["items"]) >= MAX_ITEMS:
                        add_err(fname, "item_limit", max=MAX_ITEMS)
                        continue
                if f.path is None and f.size:
                    # the part came without a spool (other parser/test) - put it on disk
                    fd, f.path = tempfile.mkstemp(prefix=".signage-upload-", dir=MEDIA)
                    with os.fdopen(fd, "wb") as out:
                        out.write(f.content)
                if not f.size:
                    add_err(fname, "empty_file")
                    continue
                cur = f.path                 # current temporary file (a new one after repacking)
                # Lossless container fix before validation decides about the
                # file - otherwise a .mov with a perfectly fine H.264 track and
                # only a container problem would be rejected.
                fname, cur, fixed = auto_repack_file(fname, cur)
                if cur != f.path:
                    f.discard()          # the original upload is no longer needed
                    f.path = cur         # discard() in the cleanup also removes the repacked one
                size = os.path.getsize(cur)
                invalid = validate_media_file(fname, cur)
                if invalid:
                    add_err(fname, "media_invalid", reason=localize(invalid, lang))
                    continue
                if size > MAX_FILE_BYTES:
                    add_err(fname, "file_too_large", max=MAX_FILE_MB)
                    continue
                warn_list = media_warnings_file(fname, cur, dev)
                reserved, reason = _reserve_upload(_media_name(fname), size)
                if reserved is None:
                    if reason == "quota":
                        add_err(fname, "storage_quota", max=MAX_TOTAL_MB)
                    else:
                        add_err(fname, "invalid_file_path")
                    continue
                name, dst = reserved
                tmp_path = None
                published = False
                committed = False
                try:
                    tmp_path = cur           # already in MEDIA (same filesystem)
                    fd = os.open(tmp_path, os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                    os.replace(tmp_path, dst)
                    tmp_path = None
                    f.path = None            # published, the cleanup must not delete it any more
                    published = True
                    with _lock:
                        d = device(dev)
                        if len(d["items"]) >= MAX_ITEMS:
                            add_err(fname, "item_limit", max=MAX_ITEMS)
                        else:
                            item = {"id": name, "file": name, "type": k,
                                    "seconds": 10 if k == "image" else 30,
                                    "sound": False, "enabled": True,
                                    "from": "", "until": ""}
                            d["items"].append(item)
                            d["version"] += 1
                            try:
                                save()
                            except Exception:
                                d["items"].pop()
                                d["version"] -= 1
                                raise
                            committed = True
                    if not committed:
                        os.unlink(dst)
                        published = False
                        continue
                    published = False
                finally:
                    if tmp_path:
                        try:
                            os.unlink(tmp_path)
                        except FileNotFoundError:
                            pass
                    if published:
                        try:
                            os.unlink(dst)
                        except FileNotFoundError:
                            pass
                    _release_upload(name, size)

                done.append(name)
                for u in fixed:
                    fixes.append(f"{name}: {localize(u, lang)}")
                for w in warn_list:
                    warns.append(f"{name}: {localize(w, lang)}")
            except ConvertBusy:
                add_err(fname, "server_busy")
                self._upload_status = (503, {"Retry-After": "5"})
            except ConvertNoSpace:
                add_err(fname, "file_save_failed")
                self._upload_status = (507, {})
            except Exception:
                add_err(fname, "file_save_failed")
        payload = {"ok": not errs, "saved": done, "errors": errs,
                   "warnings": warns, "fixes": fixes}
        if errs:
            payload["error_codes"] = codes        # parallel to `errors`
            payload["code"] = codes[0]
        return payload

    def _admin_device(self, req):
        """Return the device name from the body, or respond 400 and return None."""
        dev = req.get("device", "default")
        if not valid_device(dev):
            self._err(400, "invalid_device")
            return None
        return dev

    def _mutate(self, dev, fn):
        """Run fn(d) on the device under the lock and save the state.

        fn returns True if something changed (then version is bumped and save
        is called). If save fails, the original device (and version) is restored
        in memory and the client gets a JSON error 500 (507 on a full disk).
        Returns the result of fn, or None when the response has already been sent.
        """
        with _lock:
            devices = _state.setdefault("devices", {})
            existed = isinstance(devices, dict) and dev in devices
            snapshot = copy.deepcopy(devices.get(dev)) if existed else None
            try:
                d = device(dev)
                changed = fn(d)
                if changed:
                    d["version"] += 1
                    save()
                return {"changed": bool(changed), "device": d}
            except Exception as exc:
                if existed:
                    _state["devices"][dev] = snapshot
                else:
                    _state["devices"].pop(dev, None)
                sys.stderr.write("  state save failed (%s)\n" % type(exc).__name__)
                code = 507 if getattr(exc, "errno", None) == errno.ENOSPC else 500
        self._err(code, "state_save_failed")
        return None

    def set_settings(self, req):
        dev = self._admin_device(req)
        if dev is None:
            return
        brand = None
        if "brand" in req:
            brand, err = validate_brand(req["brand"])
            if err:
                self._err_from(400, err)
                return
        label = None
        if "label" in req:
            label, err = validate_label(req["label"])
            if err:
                self._err_from(400, err)
                return

        def apply(d):
            s = d.setdefault("settings", {})
            if "qrCorner" in req:
                s["qrCorner"] = bool(req["qrCorner"])
            if "qr1On" in req:
                s["qr1On"] = bool(req["qr1On"])
            if "qr2On" in req:
                s["qr2On"] = bool(req["qr2On"])
            if "qrUrl" in req:
                s["qrUrl"] = str(req["qrUrl"] or "")[:500].strip()
            if "qrUrl2" in req:
                s["qrUrl2"] = str(req["qrUrl2"] or "")[:500].strip()
            if "brand" in req:
                if brand:
                    merged = dict(s["brand"]) if isinstance(s.get("brand"), dict) else {}
                    merged.update(brand)
                    s["brand"] = {k: v for k, v in merged.items() if v}
                else:
                    s.pop("brand", None)
            if "label" in req:
                if label:
                    s["label"] = label
                else:
                    s.pop("label", None)
            if "transition" in req:
                # Only two predictable choices; an unknown value safely means fade.
                s["transition"] = "cut" if req["transition"] == "cut" else "fade"
            if "fit" in req:
                s["fit"] = display_mode({"fit": req["fit"]})
            return True

        res = self._mutate(dev, apply)
        if res is None:
            return
        self._json({"ok": True, "settings": res["device"].get("settings", {})})

    def qr_png(self):
        """Generate the QR for the screen target; slot=2 is the second contact (e-mail)."""
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        dev = query.get("device", ["default"])[0]
        slot = query.get("slot", ["1"])[0]
        with _lock:
            settings = _state["devices"].get(dev, {}).get("settings", {})
            brand = brand_of(settings)
            if slot == "2":
                url = qr_target((settings.get("qrUrl2") or "").strip()
                                or brand["qrEmail"])
            else:
                url = qr_target(settings.get("qrUrl", ""), brand["url"])
        if segno is None:
            # fallback: static default QR image in case segno is missing
            return self._file(asset_path("default-qr.png"))
        png = _qr_cache_get(url)
        if png is None:
            import io
            buf = io.BytesIO()
            segno.make(url, error="h").save(buf, kind="png", scale=12, border=2,
                                            dark="#101010", light="#f5f0e4")
            png = buf.getvalue()
            _qr_cache_put(url, png)
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(png)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive")
        for k, v in _cors_headers(self.headers.get("Origin", "")).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(png)

    def reorder(self, req):
        dev = self._admin_device(req)
        if dev is None:
            return
        order = req.get("order", [])
        if not isinstance(order, list) or not all(isinstance(f, str) for f in order):
            return self._err(400, "order_not_list")
        def apply(d):
            by = {i["file"]: i for i in d["items"]}
            d["items"] = [by[f] for f in order if f in by] + \
                         [i for i in d["items"] if i["file"] not in order]
            return True

        if self._mutate(dev, apply) is None:
            return
        self._json({"ok": True})

    def delete(self, req):
        dev = self._admin_device(req)
        if dev is None:
            return
        f = req.get("file", "")
        if not isinstance(f, str):
            return self._err(400, "file_not_string")
        def apply(d):
            d["items"] = [i for i in d["items"] if i["file"] != f]
            return True

        if self._mutate(dev, apply) is None:
            return
        # delete the file only if no other device uses it
        used = any(i["file"] == f for dd in _state["devices"].values()
                   for i in dd["items"])
        if not used:
            try:
                path = media_path(safe(f))
                if path is not None:
                    os.remove(path)
            except OSError:
                pass
            _remove_thumbs(safe(f))
        self._json({"ok": True})

    def edit_item(self, req):
        dev = self._admin_device(req)
        if dev is None:
            return
        f = req.get("file", "")
        if not isinstance(f, str):
            return self._err(400, "file_not_string")
        seconds = None
        if "seconds" in req:
            sec = req["seconds"]
            if (isinstance(sec, bool) or not isinstance(sec, (int, float))
                    or sec != sec or abs(sec) > MAX_ITEM_SECONDS):
                return self._err(400, "seconds_invalid", max=MAX_ITEM_SECONDS)
            seconds = max(2, int(sec))
        schedule = None
        if "schedule" in req:
            try:
                schedule = validate_schedule(req["schedule"])
            except ValueError:
                return self._err(400, "schedule_invalid")
        def apply(d):
            for i in d["items"]:
                if i["file"] == f:
                    if "schedule" in req:
                        if schedule:
                            i["schedule"] = schedule
                        else:
                            i.pop("schedule", None)
                    if "seconds" in req:
                        i["seconds"] = seconds
                    if "sound" in req:
                        i["sound"] = bool(req["sound"])
                    if "enabled" in req:
                        i["enabled"] = bool(req["enabled"])
                    if "from" in req:
                        i["from"] = str(req["from"] or "")[:10]
                    if "until" in req:
                        i["until"] = str(req["until"] or "")[:10]
                    return True
            return False

        if self._mutate(dev, apply) is None:
            return
        self._json({"ok": True})


if __name__ == "__main__":
    if not ADMIN_USER or not ADMIN_PASS:
        sys.stderr.write("  SIGNAGE_ADMIN_USER or SIGNAGE_ADMIN_PASS is missing\n")
        raise SystemExit(2)
    if ADMIN_PASS == PLACEHOLDER_ADMIN_PASS:
        sys.stderr.write("  SIGNAGE_ADMIN_PASS still has the placeholder value from .env.example; "
                         "set a long random password\n")
        raise SystemExit(2)
    os.makedirs(MEDIA, exist_ok=True)
    cleanup_stale_uploads()
    cleanup_stale_state_tmp()
    cleanup_stale_thumbs()
    load()
    if ALERT_TG_TOKEN and ALERT_TG_CHAT:
        threading.Thread(target=alert_loop, daemon=True).start()
        sys.stderr.write(f"  Alerting: Telegram, offline after {ALERT_OFFLINE_MIN} min\n")
    else:
        sys.stderr.write("  Alerting: disabled (SIGNAGE_TG_TOKEN/SIGNAGE_TG_CHAT missing)\n")
    srv = _Server((BIND, PORT), H)
    ip = os.popen("hostname -I 2>/dev/null").read().split()
    ip = ip[0] if ip else "127.0.0.1"
    sys.stderr.write(f"\n  Signage server running\n"
                     f"  Admin (end user): http://{ip}:{PORT}/admin\n"
                     f"  TV (enter on TV): http://{ip}:{PORT}/tv/default\n\n")
    sys.stderr.flush()
    srv.serve_forever()
