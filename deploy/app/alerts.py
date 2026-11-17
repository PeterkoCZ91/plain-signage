"""Pure monitoring functions: player report, blank-screen detection, offline events and alert text (sending and the loop stay in tvserver.py)."""
import os
import re
import time


BLANK_AFTER_S = 120     # how long a screen may report errors before we act on it
# Persistently low free RAM on the box: alert after RAM_ALERT_POLLS consecutive
# kiosk polls below RAM_ALERT_MB, back to "ok" only above RAM_OK_MB (hysteresis
# against flapping).
RAM_ALERT_MB = 80
RAM_ALERT_POLLS = 3
RAM_OK_MB = 120

# Box health from kiosk polls (/api/kiosk/release): query key -> (field in _online, min, max).
# Out of range or a non-integer = as if it had not arrived (the endpoint is
# polling, no 400).
_KIOSK_INT = {"ram": ("kiosk_ram", 0, 65536),
              "ram_total": ("kiosk_ram_total", 0, 65536),
              "up": ("kiosk_up", 0, 10 ** 9),
              "rssi": ("kiosk_rssi", -127, 0),
              "crash": ("kiosk_crash", 0, 10 ** 6)}
KIOSK_NETS = ("wifi", "eth", "other")
# all health fields carried by a snapshot from a single poll
KIOSK_HEALTH_FIELDS = tuple(f for f, _, _ in _KIOSK_INT.values()) + ("kiosk_ram_low", "kiosk_net")


def _render_report(query):
    """Extract from the heartbeat how the player is doing: ok/err and canvas size."""
    if not query:
        return None

    def num(key):
        try:
            return int((query.get(key) or ["0"])[0])
        except (ValueError, TypeError):
            return 0
    w, h = num("w"), num("h")
    why = (query.get("why") or [""])[0][:160]
    # Can the browser decode H.264 at all? A distro Chromium is often built
    # without proprietary codecs and then rejects EVERY video with
    # NotSupportedError - without this information it looks like a broken file
    # and people hunt for the problem on the server.
    # A missing parameter = an older player that does not report it yet (hence None).
    raw = (query.get("h264") or [""])[0]
    h264 = None if raw not in ("0", "1") else raw == "1"
    # browser storage quota "used/quota" in MB - an exhausted quota means the
    # service worker will not store the media (it is sent only when something
    # is already failing)
    quota = (query.get("quota") or [""])[0][:24]
    # The kiosk app on the box signs itself into the page (it cannot be told
    # from the User-Agent - the WebView reports the same as the vendor browser,
    # and the heartbeat goes through the service worker anyway). Empty = a
    # plain browser.
    kiosk_sig = (query.get("kiosk") or [""])[0][:16]
    # How many playlist items sit in the browser's Cache Storage, "stored/total".
    # Tells whether the screen survives a network disconnect - the server
    # cannot see that otherwise.
    cache = (query.get("cache") or [""])[0][:12]
    # Smoothness: "dropped/total" frames and the number of stalls waiting for
    # data. The server cannot find this out itself - only the browser on the
    # device sees it.
    drop = (query.get("drop") or [""])[0][:16]
    stall = num("stall")
    return {"ok": num("ok") == 1, "err": num("err"), "why": why, "h264": h264,
            "quota": quota if re.fullmatch(r"\d+/\d+", quota) else "",
            "cache": cache if re.fullmatch(r"\d+/\d+", cache) else "",
            "drop": drop if re.fullmatch(r"\d+/\d+", drop) else "",
            "stall": stall if stall > 0 else 0,
            "kiosk": kiosk_sig if re.fullmatch(r"[\w.\-]{1,16}", kiosk_sig) else "",
            "screen": {"w": w, "h": h} if w > 0 and h > 0 else None}


def _kiosk_int(raw, lo, hi):
    """Integer from the query within <lo, hi>, otherwise None (no exceptions)."""
    if not isinstance(raw, str) or not re.fullmatch(r"-?[0-9]{1,10}", raw):
        return None
    v = int(raw)
    return v if lo <= v <= hi else None


def _kiosk_health(query):
    """Box health snapshot from a kiosk poll, or None if the poll carries none of it
    (older kiosk). Invalid values are None, never an exception.

    rssi applies to Wi-Fi only: the kiosk does not send it otherwise, and if it
    arrives with another network type it is dropped (a stale value after
    switching to a cable would mislead).
    """
    if not query:
        return None

    def raw(key):
        v = query.get(key)
        return v[0] if v else None
    out = {f: _kiosk_int(raw(k), lo, hi) for k, (f, lo, hi) in _KIOSK_INT.items()}
    low = raw("ram_low")
    out["kiosk_ram_low"] = None if low not in ("0", "1") else low == "1"
    net = raw("net")
    out["kiosk_net"] = net if net in KIOSK_NETS else None
    if out["kiosk_net"] not in (None, "wifi"):
        out["kiosk_rssi"] = None
    if all(v is None for v in out.values()):
        return None
    return out


def kiosk_health_update(entry, health, now):
    """Write the health snapshot into the _online record (memory only, nothing to disk).

    A box restart is detected from a drop in uptime (elapsedRealtime is
    monotonic, so a smaller value than last time = the box has booted again in
    the meantime). Counted only since server start; the first report does not
    count as a restart, it just establishes the boot time.
    Also counts consecutive low-RAM polls for the alert.
    """
    old_up = entry.get("kiosk_up")
    up = health.get("kiosk_up")
    for k in KIOSK_HEALTH_FIELDS:
        entry[k] = health.get(k)
    entry["kiosk_seen"] = now
    entry.setdefault("kiosk_boots", 0)
    if up is not None:
        if isinstance(old_up, int) and up < old_up:
            entry["kiosk_boots"] = int(entry.get("kiosk_boots") or 0) + 1
            entry["kiosk_last_boot"] = now - up
        elif entry.get("kiosk_last_boot") is None:
            entry["kiosk_last_boot"] = now - up
    ram = health.get("kiosk_ram")
    if ram is not None:
        entry["kiosk_ram_low_polls"] = (int(entry.get("kiosk_ram_low_polls") or 0) + 1
                                    if ram < RAM_ALERT_MB else 0)


def is_blank(seen, now):
    """The screen reports in, but has shown nothing for a long time - a different failure than offline."""
    if not seen:
        return False
    last_seen = int(seen.get("last_seen", 0))
    if not last_seen or now - last_seen > 45:
        return False                            # offline has its own alert
    if int(seen.get("err", 0)) <= 0:
        return False                            # the player reports no error
    last_ok = int(seen.get("last_ok", 0))
    return (now - last_ok > BLANK_AFTER_S) if last_ok else True



def alert_events(online, alerted, now, offline_after):
    """Compute online/offline transitions for notifications. Pure function.

    online:  {device: {"last_seen": unix_time, ...}}
    alerted: set of devices already reported as offline
    Returns (events, new_alerted_set).
    """
    events = []
    alerted = set(alerted)
    for dev in sorted(online):
        info = online[dev]
        last = int(info.get("last_seen", 0))
        if not last:
            continue
        if now - last >= offline_after:
            if dev not in alerted:
                events.append(("offline", dev, last))
                alerted.add(dev)
            continue                    # offline is already reported, we do not care about blank
        if dev in alerted:
            events.append(("online", dev, last))
            alerted.discard(dev)
        # A blank screen is a different failure than offline: the device
        # responds, but there is nothing on it. This is exactly what nobody
        # used to notice.
        key = dev + "\0blank"
        if is_blank(info, now):
            if key not in alerted:
                events.append(("blank", dev, last))
                alerted.add(key)
        elif key in alerted:
            events.append(("rendering", dev, last))
            alerted.discard(key)
        # Persistently low RAM on the box (reported by the kiosk): a precursor of WebView crashes.
        key = dev + "\0ram"
        if int(info.get("kiosk_ram_low_polls") or 0) >= RAM_ALERT_POLLS:
            if key not in alerted:
                events.append(("low_ram", dev, last))
                alerted.add(key)
        elif key in alerted:
            ram = info.get("kiosk_ram")
            if isinstance(ram, int) and ram >= RAM_OK_MB:
                events.append(("ram_ok", dev, last))
                alerted.discard(key)
    return events, alerted


ALERT_TEXTS = {
    "en": {
        "offline": '[CRITICAL] Screen "{dev}" is offline (last seen {when}).',
        "blank": '[WARNING] Screen "{dev}" is responding but shows nothing - '
                 'the player reports that the content cannot be displayed.',
        "low_ram": '[WARNING] The box of screen "{dev}" has persistently low free RAM '
                   '(below {ram} MB) - the player may crash.',
        "ram_ok": '[OK] The box of screen "{dev}" has enough free RAM again.',
        "rendering": '[OK] Screen "{dev}" is showing something again.',
        "online": '[OK] Screen "{dev}" is online again.',
    },
    "cs": {
        "offline": "[CRITICAL] Obrazovka „{dev}“ je offline (naposledy viděna {when}).",
        "blank": "[WARNING] Obrazovka „{dev}“ odpovídá, ale nic na ní není – "
                 "přehrávač hlásí, že obsah nejde zobrazit.",
        "low_ram": "[WARNING] Box obrazovky „{dev}“ má trvale málo volné paměti RAM "
                   "(pod {ram} MB) – hrozí pády přehrávače.",
        "ram_ok": "[OK] Box obrazovky „{dev}“ má zase dost volné paměti RAM.",
        "rendering": "[OK] Obrazovka „{dev}“ zase něco zobrazuje.",
        "online": "[OK] Obrazovka „{dev}“ je zase online.",
    },
}


def alert_lang():
    """Alert language: English unless SIGNAGE_ALERT_LANG is set to cs."""
    return "cs" if os.environ.get("SIGNAGE_ALERT_LANG", "").strip().lower().startswith("cs") else "en"


def format_alert(event, lang=None):
    state, dev, last = event
    lang = lang if lang in ALERT_TEXTS else alert_lang()
    texts = ALERT_TEXTS[lang]
    key = state if state in texts else "online"
    fmt = "%d.%m. %H:%M" if lang == "cs" else "%Y-%m-%d %H:%M"
    when = time.strftime(fmt, time.localtime(last)) if key == "offline" else ""
    return texts[key].format(dev=dev, when=when, ram=RAM_ALERT_MB)
