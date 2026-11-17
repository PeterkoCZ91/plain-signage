"""State format: schema version, migration, normalization of loaded JSON and device name validation (no I/O, no global state)."""
import re

from mediautil import kind, safe
from validators import clean_schedule


STATE_SCHEMA_VERSION = 1


DEVICE_RE = re.compile(r"^[A-Za-z0-9_-]{1,48}$")


def _empty_state():
    return {"schema_version": STATE_SCHEMA_VERSION, "devices": {}, "pairs": {}}


def _migrate_state(raw):
    if not isinstance(raw, dict):
        raise ValueError("state root is not an object")
    try:
        version = int(raw.get("schema_version", 0))
    except (TypeError, ValueError):
        raise ValueError("invalid state schema version")
    if version < 0 or version > STATE_SCHEMA_VERSION:
        raise ValueError("unsupported state schema version")
    migrated = dict(raw)
    migrated["schema_version"] = STATE_SCHEMA_VERSION
    return migrated


def _normalize_state(raw):
    """Return a safe, serializable shape of the state after loading from disk."""
    if not isinstance(raw, dict):
        raw = {}
    normalized = _empty_state()
    devices = raw.get("devices")
    if isinstance(devices, dict):
        for name, value in devices.items():
            if not valid_device(name) or not isinstance(value, dict):
                continue
            items = value.get("items")
            clean_items = []
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    filename = item.get("file")
                    if (not isinstance(filename, str) or not filename
                            or safe(filename) != filename or not kind(filename)):
                        continue
                    clean = dict(item)
                    clean["id"] = str(clean.get("id") or filename)
                    clean["file"] = filename
                    clean["type"] = kind(filename)
                    try:
                        seconds = int(clean.get("seconds", 10))
                    except (TypeError, ValueError):
                        seconds = 10
                    clean["seconds"] = max(2, min(seconds, 86400))
                    clean["sound"] = bool(clean.get("sound", False))
                    clean["enabled"] = bool(clean.get("enabled", True))
                    clean["from"] = str(clean.get("from", "") or "")[:10]
                    clean["until"] = str(clean.get("until", "") or "")[:10]
                    sched = clean_schedule(clean.pop("schedule", None))
                    if sched:
                        clean["schedule"] = sched
                    clean_items.append(clean)
            try:
                version = max(0, int(value.get("version", 0)))
            except (TypeError, ValueError):
                version = 0
            clean_device = dict(value)
            clean_device["version"] = version
            clean_device["items"] = clean_items
            if not isinstance(clean_device.get("settings"), dict):
                clean_device["settings"] = {}
            # required kiosk update = versionCode (int > 0), otherwise drop it
            ku = clean_device.get("kiosk_update")
            if "kiosk_update" in clean_device and (
                    isinstance(ku, bool) or not isinstance(ku, int) or ku <= 0):
                del clean_device["kiosk_update"]
            normalized["devices"][name] = clean_device

    pairs = raw.get("pairs")
    if isinstance(pairs, dict):
        for code, value in pairs.items():
            if (not isinstance(code, str)
                    or not re.fullmatch(r"[A-Z2-9]{6}", code)
                    or not isinstance(value, dict)):
                continue
            client = value.get("client", "")
            try:
                expires = int(value.get("expires", 0))
            except (TypeError, ValueError):
                continue
            if not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", str(client)):
                continue
            pair = {"client": str(client), "expires": expires}
            if valid_device(value.get("device")):
                pair["device"] = value["device"]
            normalized["pairs"][code] = pair
    return normalized


def valid_device(name):
    return isinstance(name, str) and bool(DEVICE_RE.fullmatch(name))
