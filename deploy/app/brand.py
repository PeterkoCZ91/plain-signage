"""Screen brand, QR targets and cards, content orientation and playlist settings (pure functions over the settings dict)."""
import os
import re

from errors import ErrText


def _load_brand_file():
    """Optional client layer: KEY=VALUE lines (SIGNAGE_BRAND_*) in brand.env.

    Looked up at SIGNAGE_BRAND_FILE, else next to the state file (the data
    directory). A real environment variable always wins over the file.
    """
    path = os.environ.get("SIGNAGE_BRAND_FILE") or os.path.join(os.path.dirname(
        os.environ.get("SIGNAGE_STATE")
        or os.path.join(os.path.dirname(os.path.abspath(__file__)), "tv-state.json")),
        "brand.env")
    out = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                if key.startswith("SIGNAGE_BRAND_"):
                    out[key] = val
    except OSError:
        pass
    return out


_FILE = _load_brand_file()


def _env(key, default):
    return os.environ.get(key, _FILE.get(key, default))


# Default brand: neutral demo values. The deployment overrides them with env
# SIGNAGE_BRAND_* or with brand.env in the data directory, and per device with
# the settings["brand"] entry.
# Not editable in the admin (deployment only): wordmark ("LEAD|REST", the part
# before | is bold; empty = the name in capitals), taglineCs/taglineEn (empty =
# the player's built-in neutral text) and the optional extra QR shortcut.
BRAND_DEFAULTS = {
    "name": _env("SIGNAGE_BRAND_NAME", "Your Brand"),
    "email": _env("SIGNAGE_BRAND_EMAIL", "info@example.com"),
    "phone": _env("SIGNAGE_BRAND_PHONE", "+000 000 000 000"),
    "city": _env("SIGNAGE_BRAND_CITY", "Your City"),
    "url": _env("SIGNAGE_BRAND_URL", "https://example.com/"),
    "qrEmail": _env("SIGNAGE_BRAND_QR_EMAIL", "info@example.com"),
    "pwaTitle": _env("SIGNAGE_BRAND_PWA_TITLE", "Signage"),
    "wordmark": _env("SIGNAGE_BRAND_WORDMARK", ""),
    "taglineCs": _env("SIGNAGE_BRAND_TAGLINE_CS", ""),
    "taglineEn": _env("SIGNAGE_BRAND_TAGLINE_EN", ""),
    "extraQrUrl": _env("SIGNAGE_BRAND_EXTRA_QR_URL", ""),
    "extraQrLabelCs": _env("SIGNAGE_BRAND_EXTRA_QR_LABEL_CS", ""),
    "extraQrLabelEn": _env("SIGNAGE_BRAND_EXTRA_QR_LABEL_EN", ""),
}
BRAND_MAX = {"name": 60, "email": 120, "phone": 40, "city": 80,
             "url": 500, "qrEmail": 120, "pwaTitle": 30}
DEFAULT_QR_URL = BRAND_DEFAULTS["url"]
DEFAULT_QR_EMAIL = BRAND_DEFAULTS["qrEmail"]
# Built-in placeholder targets: a QR pointing at them is never shown (an unset brand
# gets the neutral empty screen instead, see the player).
PLACEHOLDER_QR_TARGETS = ("https://example.com/", "info@example.com", "mailto:info@example.com")


# --- QR code target -------------------------------------------------------------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def brand_of(settings):
    """Effective device brand: stored non-empty strings over the defaults.

    A missing, broken or foreign brand in the (old) state = defaults.
    """
    stored = settings.get("brand") if isinstance(settings, dict) else None
    out = dict(BRAND_DEFAULTS)
    if isinstance(stored, dict):
        for key in BRAND_MAX:
            val = stored.get(key)
            if isinstance(val, str) and val.strip():
                out[key] = val.strip()
    return out


def _brand_text_ok(val):
    return not any(ch in val for ch in "<>") and val.isprintable()


def _brand_url_ok(val, allow_web):
    low = val.lower()
    if low.startswith("mailto:"):
        return bool(EMAIL_RE.fullmatch(val[7:]))
    if EMAIL_RE.fullmatch(val):
        return True
    return allow_web and bool(re.fullmatch(r"https?://[^\s<>\"'`]+", val, re.I))


def validate_brand(raw):
    """Validate a brand from the API. Returns (clean_dict, None) or (None, error).

    Empty string = reset the field to default; None = delete the whole brand.
    No HTML (<, >), URL only http(s)/mailto/bare e-mail.
    """
    if raw is None:
        return {}, None
    if not isinstance(raw, dict):
        return None, ErrText("brand_not_object")
    clean = {}
    for key, val in raw.items():
        if key not in BRAND_MAX:
            continue
        if not isinstance(val, str):
            return None, ErrText("brand_field_not_text", field=key)
        val = val.strip()
        if len(val) > BRAND_MAX[key]:
            return None, ErrText("brand_field_too_long", field=key)
        if val:
            if not _brand_text_ok(val):
                return None, ErrText("brand_field_bad_chars", field=key)
            if key in ("email", "qrEmail"):
                if not _brand_url_ok(val, False):
                    return None, ErrText("brand_field_not_email", field=key)
            elif key == "url" and not _brand_url_ok(val, True):
                return None, ErrText("brand_url_invalid")
        clean[key] = val
    return clean, None


def qr_target(url, default=None):
    """What is actually encoded into the QR - a bare e-mail gets mailto:."""
    url = (url or "").strip() or default or DEFAULT_QR_URL
    if not url.lower().startswith("mailto:") and EMAIL_RE.fullmatch(url):
        return "mailto:" + url
    return url


def qr_caption(url, default=None):
    """(kind, title, caption) for a QR by target: IG handle, full e-mail, domain.

    The title is an English default; the player maps it to its own language
    (en/cs, see QR_DEFAULT_LABELS in player.html) and shows a custom label as is.
    """
    url = (url or "").strip() or default or DEFAULT_QR_URL
    if url.lower().startswith("mailto:"):
        url = url[len("mailto:"):]
    if EMAIL_RE.fullmatch(url):
        return ("email", "Write to us", url)
    m = re.match(r"^https?://(?:www\.)?instagram\.com/([^/?#]+)", url, re.I)
    if m:
        return ("instagram", "Follow us on Instagram", "@" + m.group(1))
    m = re.match(r"^https?://(?:www\.)?([^?#]*)", url, re.I)
    if m:
        return ("web", "Find more here", m.group(1).rstrip("/"))
    return ("web", "Scan to take a look", url)


def qr_cards(settings):
    """QR cards for the idle screen (stacked): IG + e-mail, each can be switched off.

    Disabled slots are skipped, identical targets are merged into one card.
    """
    cards = []
    seen = set()
    brand = brand_of(settings)
    slots = ((settings.get("qr1On", True), settings.get("qrUrl", ""), brand["url"], 1),
             (settings.get("qr2On", True), settings.get("qrUrl2", ""), brand["qrEmail"], 2))
    for on, stored, fallback, slot in slots:
        if not on:
            continue
        url = (stored or "").strip() or fallback
        if url in PLACEHOLDER_QR_TARGETS:
            continue
        target = qr_target(url, brand["url"])
        if target in seen:
            continue
        seen.add(target)
        card_kind, label, handle = qr_caption(url, brand["url"])
        if card_kind == "instagram":
            handle = ""          # the logo already says the name - do not repeat it under the QR
        cards.append({"url": url, "kind": card_kind, "label": label,
                      "handle": handle, "slot": slot})
    return cards


DISPLAY_MODES = ("landscape", "portrait", "portrait270", "fill")
# landscape (default) / portrait / portrait rotated the other way / fill the canvas.
# Two rotation directions because a screen can be mounted on either side and
# the content would otherwise be upside down.


def display_mode(settings):
    """Content orientation on the screen.

    Unknown or missing values mean the current behaviour (landscape) - badly
    stored settings must not break the screen at the client's.
    """
    choice = settings.get("fit")
    return choice if choice in DISPLAY_MODES else "landscape"


def playlist_settings(settings):
    """Screen settings as the player receives them in the playlist."""
    brand = brand_of(settings)
    qr_kind, qr_label, qr_handle = qr_caption(settings.get("qrUrl", ""), brand["url"])
    return {"brand": brand,
            "qrCorner": bool(settings.get("qrCorner", False)),
            "qr1On": bool(settings.get("qr1On", True)),
            "qr2On": bool(settings.get("qr2On", True)),
            "qrUrl": settings.get("qrUrl", "") or brand["url"],
            "qrUrl2": settings.get("qrUrl2", ""),
            "qrCards": qr_cards(settings),
            "qrKind": qr_kind, "qrLabel": qr_label, "qrHandle": qr_handle,
            "transition": "cut" if settings.get("transition") == "cut" else "fade",
            "fit": display_mode(settings)}
