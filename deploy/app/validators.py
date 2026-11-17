"""Pure input validation: item schedule and screen label."""
import re
import unicodedata

from errors import ErrText


_HHMM = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


def validate_schedule(v):
    """Item schedule -> normalized dict, or None (= no restriction).

    Raises ValueError on invalid input. `None`, `{}` and `{"days": []}` without
    times mean "no schedule". Days 0=Monday..6=Sunday, unique; `from` and `to`
    (HH:MM) only together and different.
    """
    if v is None:
        return None
    if not isinstance(v, dict) or set(v) - {"days", "from", "to"}:
        raise ValueError("schedule")
    out = {}
    days = v.get("days")
    if days is not None:
        if (not isinstance(days, list) or len(set(days)) != len(days)
                or any(isinstance(x, bool) or not isinstance(x, int)
                       or not 0 <= x <= 6 for x in days)):
            raise ValueError("days")
        if days:
            out["days"] = sorted(days)
    has_from, has_to = "from" in v, "to" in v
    if has_from != has_to:
        raise ValueError("from/to")
    if has_from:
        a, b = v["from"], v["to"]
        if (not isinstance(a, str) or not isinstance(b, str)
                or not _HHMM.match(a) or not _HHMM.match(b) or a == b):
            raise ValueError("time")
        out["from"], out["to"] = a, b
    return out or None


def clean_schedule(v):
    """Tolerant variant for loading state: an invalid schedule is dropped."""
    try:
        return validate_schedule(v)
    except ValueError:
        return None




LABEL_MAX = 60


def validate_label(raw):
    """Human-readable screen label -> (value, error). Empty = no label.

    No HTML: the characters < and > and control characters are rejected (the
    admin also inserts the label only via textContent).
    """
    if raw is None:
        return "", None
    if not isinstance(raw, str):
        return None, ErrText("label_not_text")
    text = " ".join(raw.split())
    if len(text) > LABEL_MAX:
        return None, ErrText("label_too_long", max=LABEL_MAX)
    if any(c in "<>" or unicodedata.category(c).startswith("C") for c in text):
        return None, ErrText("label_invalid_chars")
    return text, None


KIOSK_NAME_MAX = 32
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
KIOSK_CODE_MAX = 2 ** 31 - 1       # Android versionCode is an int


def _pos_int(v, top=None):
    return (isinstance(v, int) and not isinstance(v, bool) and v > 0
            and (top is None or v <= top))


def validate_kiosk_release(raw):
    """Kiosk release descriptor (kiosk.json) -> normalized dict, or None.

    Expects {"versionCode": int>0, "versionName": str<=32, "sha256": 64
    lowercase hex, "size": int>0}. Other keys are ignored. The caller checks
    that `size` matches the real APK (this function does not touch the disk).
    """
    if not isinstance(raw, dict):
        return None
    code, name = raw.get("versionCode"), raw.get("versionName")
    sha, size = raw.get("sha256"), raw.get("size")
    if not _pos_int(code, KIOSK_CODE_MAX) or not _pos_int(size):
        return None
    if (not isinstance(name, str) or not name or len(name) > KIOSK_NAME_MAX
            or any(c in "<>" or unicodedata.category(c).startswith("C") for c in name)):
        return None
    if not isinstance(sha, str) or not _SHA256_RE.match(sha):
        return None
    return {"versionCode": code, "versionName": name, "sha256": sha, "size": size}
