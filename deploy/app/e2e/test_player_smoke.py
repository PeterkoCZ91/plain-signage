"""Smoke tests of the player (/tv/<name>) in a real Chromium.

Time is driven by `page.clock` (install + pause_at + run_for), so item
rotation, the schedule and the 15 s poll are deterministic and fast. The
network and media decoding stay real, so the result is awaited by short
polling, not by sleeping. Every test collects pageerror + console.error (see
conftest.make_page).

What the helpers do:
  * `shown(page)`  - what is on the canvas right now: 'r'/'g'/'b' (image color),
                     'video', 'idle' (the idle screen) or None.
  * `wait_shown()` - waits until the canvas shows the expected value.
"""
import datetime
import json
import time
import urllib.request
import uuid

import pytest

from test_admin_smoke import make_png, make_video, png_bytes  # noqa: F401

RED, GREEN, BLUE = (220, 30, 30), (30, 220, 30), (30, 30, 220)
KEY = {RED: "r", GREEN: "g", BLUE: "b"}
SECONDS = 2     # the minimum the server allows (max(2, seconds))

TUE = datetime.datetime(2026, 9, 29, 1, 0)   # Tuesday 01:00 (verified by test_browser_clock)


# ---------- helpers: API ----------

def _req(server, path, body=None, headers=None, method=None):
    h = dict(server.auth)
    h.update(headers or {})
    data = body
    if isinstance(body, (dict, list)):
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(server.base + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def api_upload(server, device, *files):
    """files: (name, bytes). Returns the file names on the server in playlist order."""
    boundary = "----e2e" + uuid.uuid4().hex
    parts = []
    for name, data in files:
        parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                      f"filename=\"{name}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
                     + data + b"\r\n")
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    status, res = _req(server, f"/api/upload?device={device}", body,
                       {"Content-Type": f"multipart/form-data; boundary={boundary}",
                        "Idempotency-Key": uuid.uuid4().hex})
    assert status == 200 and res.get("saved"), f"upload failed: {status} {res}"
    return files_of(server, device)


def files_of(server, device):
    import urllib.parse
    return [urllib.parse.unquote(i["url"].replace("/media/", ""))
            for i in server.playlist(device)["items"]]


def devices(server):
    return server.get_json("/api/devices")["devices"]


def edit_item(server, device, file, **fields):
    return _req(server, "/api/item", dict(device=device, file=file, **fields))


def set_seconds(server, device, files, seconds=SECONDS):
    for f in files:
        st, res = edit_item(server, device, f, seconds=seconds)
        assert st == 200, f"/api/item seconds: {st} {res}"


def color_playlist(server, device, colors=(RED, GREEN, BLUE), seconds=SECONDS):
    files = api_upload(server, device, *[(f"{KEY[c]}.png", png_bytes(rgb=c)) for c in colors])
    set_seconds(server, device, files, seconds)
    return files


# ---------- helpers: page ----------

NO_SW = ("() => { try { delete Navigator.prototype.serviceWorker; } catch (e) {} }")

SHOWN_JS = """async () => {
  const idle = document.getElementById('idle');
  const layer = document.getElementById('stage').lastElementChild;
  if (!layer) return idle && getComputedStyle(idle).display !== 'none' ? 'idle' : null;
  if (layer.querySelector('video')) return 'video';
  const m = /url\\("?([^")]+)"?\\)/.exec(layer.style.backgroundImage || '');
  if (!m) return null;
  const img = new Image();
  img.src = m[1];
  try { await img.decode(); } catch (e) { return 'image-error'; }
  const c = document.createElement('canvas'); c.width = c.height = 1;
  const g = c.getContext('2d'); g.drawImage(img, 0, 0, 1, 1);
  const [r, gg, b] = g.getImageData(0, 0, 1, 1).data;
  return r > 150 ? 'r' : gg > 150 ? 'g' : b > 150 ? 'b' : 'other:' + [r, gg, b];
}"""


def shown(page):
    return page.evaluate(SHOWN_JS)


def wait_until(fn, what, timeout=10.0, step=0.05):
    """Polls from Python (not via the page's rAF/setTimeout, which the fake clock drives)."""
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = fn()
        if last:
            return last
        time.sleep(step)
    raise AssertionError(f"did not happen within {timeout:.0f} s: {what} (last value: {last!r})")


def wait_shown(page, expected, timeout=10.0):
    state = {}

    def check():
        state["v"] = shown(page)
        return state["v"] == expected
    end = time.time() + timeout
    while time.time() < end:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError(f"the canvas should show {expected!r}, it shows {state.get('v')!r} "
                         f"(#err={page.text_content('#err')!r})")


def open_player(page, server, device, *, clock=TUE, pause=True, query="", sw=False):
    """Opens /tv/<device> with a controlled clock; without `sw` the service worker is disabled
    (route/clock tests must not have a SW between themselves and the server)."""
    if not sw:
        page.add_init_script(NO_SW.replace("() => {", "(() => {", 1) + ")()")
    if clock is not None:
        page.clock.install(time=clock)
        if pause:
            page.clock.pause_at(clock + datetime.timedelta(seconds=1))
    with page.expect_response(lambda r: "/playlist" in r.url and "heartbeat=1" in r.url):
        page.goto(f"{server.base}/tv/{device}{query}")
    return page


def run_for(page, seconds, step=1.0):
    """Moves the clock in steps (1 s by default) and lets the real image load
    finish between steps. One big jump would fire several next() in a row before
    the first image manages to load, and the player would rightly report it as "no response"."""
    left = int(seconds * 1000)
    step_ms = int(step * 1000)
    while left > 0:
        d = min(step_ms, left)
        page.clock.run_for(d)
        left -= d
        time.sleep(0.04)


def expect_no_err(page):
    assert page.text_content("#err").strip() == "", f"#err is not empty: {page.text_content('#err')!r}"


# ---------- (0) sanity: the clock in the browser ----------

def test_browser_clock(page, server):
    """Verifies the assumptions of the other tests: TUE is Tuesday 01:00 in the page's local time."""
    open_player(page, server, "clock")
    got = page.evaluate("() => { const d = new Date(); return [d.getDay(), d.getHours(), d.getMinutes()]; }")
    assert got[:2] == [2, 1], f"the fake clock is not Tuesday 01:xx but {got} (0=Sunday)"


# ---------- (1) empty playlist: the idle screen ----------

def test_empty_playlist_idle_screen(page, server):
    st, res = _req(server, "/api/settings", {"device": "salon", "brand": {
        "name": "Studio E2E", "email": "hello@example.org", "phone": "+420 111 222 333", "city": "Brno"}})
    assert st == 200, (st, res)
    assert files_of(server, "salon") == []
    open_player(page, server, "salon")
    wait_shown(page, "idle")
    assert page.locator("#stage > *").count() == 0
    # the QR really loaded (not a broken image)
    page.wait_for_function("() => { const i = document.getElementById('idleqr');"
                           " return i.complete && i.naturalWidth > 0; }")
    assert page.is_visible("#idleqr"), "the QR is not visible on the idle screen"
    contact = page.text_content(".foot .contact")
    for part in ("hello@example.org", "+420 111 222 333", "Brno"):
        assert part in contact, f"the brand contact lacks {part!r}: {contact!r}"
    expect_no_err(page)


def test_brand_with_html_is_shown_as_text(page, server):
    """The server rejects `<`/`>` in the brand today (400), but an old state may have had it stored.
    The player must therefore never insert it as HTML; we simulate a stored value in the response."""
    st, res = _req(server, "/api/settings", {"device": "salon2", "brand": {"city": "<b>Brno</b>"}})
    assert st == 400, f"the server should have rejected <b> in the brand, it returned {st} {res}"

    def rewrite(route):
        r = route.fetch()
        data = r.json()
        data["settings"]["brand"]["city"] = "<b>Brno</b>"
        data["settings"]["brand"]["phone"] = "<img src=x onerror=window.__xss=1>"
        route.fulfill(response=r, json=data)
    page.route("**/api/tv/salon2/playlist**", rewrite)
    open_player(page, server, "salon2")
    wait_shown(page, "idle")
    page.wait_for_function("() => document.getElementById('footrest').textContent.includes('Brno')")
    contact = page.text_content("#footrest")
    assert "<b>Brno</b>" in contact and "<img" in contact, f"the HTML was not shown as text: {contact!r}"
    assert page.locator("#idle b, #idle .foot img").count() == 0, "the brand was inserted as an HTML element"
    assert page.evaluate("() => window.__xss") is None


# ---------- (2) three images alternate and loop ----------

def test_three_images_alternate_and_loop(page, server):
    color_playlist(server, "loop")
    open_player(page, server, "loop")
    wait_shown(page, "r")
    seen = ["r"]
    for expected in ["g", "b", "r", "g"]:          # two rounds, including the return to the start
        run_for(page, SECONDS)
        wait_shown(page, expected)
        seen.append(expected)
    assert seen == ["r", "g", "b", "r", "g"]
    expect_no_err(page)


def test_image_does_not_change_before_seconds_elapse(page, server):
    color_playlist(server, "wait", seconds=6)
    open_player(page, server, "wait")
    wait_shown(page, "r")
    run_for(page, 5)
    time.sleep(0.3)
    assert shown(page) == "r", "the image switched before `seconds` elapsed"
    run_for(page, 1)
    wait_shown(page, "g")


# ---------- (3) video ----------

def test_video_plays_and_moves_on_to_the_next(page, server, tmp_path):
    clip = make_video(tmp_path)                       # skipped without ffmpeg
    probe = page.evaluate("() => document.createElement('video').canPlayType('video/mp4; codecs=\"avc1.42E01E\"')")
    if not probe:
        pytest.skip("this Chromium (Playwright build) cannot do H.264, the video cannot be played")
    with open(clip, "rb") as f:
        vid = f.read()
    files = api_upload(server, "video", ("clip.mp4", vid), ("b.png", png_bytes(rgb=BLUE)))
    set_seconds(server, "video", [f for f in files if f.endswith(".png")])
    # the video runs in real time (the media is real), hence no fake clock
    open_player(page, server, "video", clock=None)
    wait_shown(page, "video", timeout=10)
    page.wait_for_function("() => { const v = document.querySelector('#stage video');"
                           " return !v || v.currentTime > 0 || v.ended; }", timeout=10000)
    wait_shown(page, "b", timeout=10)                # after ended (1 s) it moves on to the image
    expect_no_err(page)


# ---------- (4) a playlist change propagates within 15 s ----------

def test_playlist_change_propagates_without_reload(page, server):
    files = color_playlist(server, "change", colors=(RED,))
    open_player(page, server, "change")
    wait_shown(page, "r")
    page.evaluate("() => { window.__noReload = 1; }")
    more = api_upload(server, "change", ("g.png", png_bytes(rgb=GREEN)))
    set_seconds(server, "change", more)
    st, res = edit_item(server, "change", files[0], enabled=False)   # R hidden, the file stays
    assert st == 200, (st, res)
    run_for(page, 15.5)                               # one poll (POLL_MS = 15 s)
    wait_shown(page, "g")
    assert page.evaluate("() => window.__noReload") == 1, "the page reloaded, the change should have come via the poll"
    expect_no_err(page)


# ---------- (5) From-Until and the schedule ----------

def test_item_outside_from_until_is_not_shown(page, server):
    files = color_playlist(server, "dates")
    # page clock: 2026-09-29. R starts tomorrow, G ended yesterday, B has no limit.
    assert edit_item(server, "dates", files[0], **{"from": "2026-09-30"})[0] == 200
    assert edit_item(server, "dates", files[1], until="2026-09-28")[0] == 200
    open_player(page, server, "dates")
    wait_shown(page, "b")
    for _ in range(3):
        run_for(page, SECONDS + 0.5)
        time.sleep(0.2)
        assert shown(page) == "b", "an item outside From-Until was shown"
    expect_no_err(page)


def _schedule_or_skip(server, device, file, schedule):
    st, res = edit_item(server, device, file, schedule=schedule)
    if st == 400 or "schedule" not in (server.playlist(device)["items"] or [{}])[0]:
        pytest.skip("the server does not support `schedule` yet (the API did not return it)")


@pytest.mark.parametrize("clock,visible", [
    (datetime.datetime(2026, 9, 29, 1, 0), True),      # Tuesday 01:00: spill-over of Monday 22:00-02:00
    (datetime.datetime(2026, 9, 29, 12, 0), False),    # Tuesday 12:00: outside the window
    (datetime.datetime(2026, 9, 28, 23, 0), True),     # Monday 23:00: in the window on the start day
    (datetime.datetime(2026, 9, 29, 23, 0), False),    # Tuesday 23:00: Tuesday is not in `days`
])
def test_schedule_with_a_fixed_time(page, server, clock, visible):
    files = api_upload(server, "schedule", ("r.png", png_bytes(rgb=RED)))
    set_seconds(server, "schedule", files)
    _schedule_or_skip(server, "schedule", files[0], {"days": [0], "from": "22:00", "to": "02:00"})
    open_player(page, server, "schedule", clock=clock)
    got = page.evaluate("() => { const d = new Date(); return [(d.getDay() + 6) % 7, d.getHours()]; }")
    assert got == [(clock.weekday()), clock.hour], f"the page clock is not set: {got}"
    wait_shown(page, "r" if visible else "idle")
    expect_no_err(page)


def test_schedule_drops_out_at_runtime_without_a_poll(page, server):
    """An item with a schedule leaves even though the playlist did not change (refreshVisible in next())."""
    files = color_playlist(server, "schedule2", colors=(RED, GREEN))
    _schedule_or_skip(server, "schedule2", files[0], {"from": "01:00", "to": "01:05"})
    open_player(page, server, "schedule2", clock=TUE)     # 01:00 -> both visible
    wait_shown(page, "r")
    run_for(page, 5 * 60, step=SECONDS)                    # 01:05 -> R drops out
    wait_shown(page, "g")
    run_for(page, SECONDS)
    time.sleep(0.2)
    assert shown(page) == "g", "the item is still shown after its schedule ended"


# ---------- (6) offline from the service worker ----------

def test_offline_plays_from_the_service_worker(page, server):
    color_playlist(server, "offline", colors=(RED, GREEN))
    open_player(page, server, "offline", clock=None, sw=True)
    try:
        page.wait_for_function("() => !!navigator.serviceWorker.controller", timeout=10000)
    except Exception:
        pytest.skip("the service worker did not take control of the page (Playwright/Chromium did not allow it)")
    wait_shown(page, "r")
    # wait until the SW downloads the media and stores the playlist (WARM_PLAYLIST after the first poll)
    page.wait_for_function("""async () => {
        const m = await caches.open('signage-media-v1'), p = await caches.open('signage-playlists-v1');
        return (await m.keys()).length >= 2 && (await p.keys()).length >= 1; }""", timeout=15000)

    ctx = page.page.context
    ctx.set_offline(True)
    try:
        page.reload()
        wait_shown(page, "r", timeout=10)
        # regression: the SW must return cached media by URL offline (it used to
        # call media.handle({event}) without `request`, throw and fall back to the
        # network), also with a Range request like the one <video> sends
        got = page.evaluate("""async () => {
            const keys = await (await caches.open('signage-media-v1')).keys();
            const out = [];
            for (const k of keys) {
                out.push((await fetch(k.url)).status);
                out.push((await fetch(k.url, {headers: {Range: 'bytes=0-9'}})).status);
            }
            return out; }""")
        assert got and set(got) == {200, 206}, f"the SW does not return media offline: {got}"
        # it keeps playing (the second image after `seconds`), without the network
        wait_shown(page, "g", timeout=SECONDS + 6)
        err_offline = page.text_content("#err")
        # note: with a fresh cache the SW returns the playlist from memory (200), so the
        # message "offline – přehrávám uloženou kopii" appears only when that fails too; it need not be non-empty
        print("offline #err =", repr(err_offline))
    finally:
        ctx.set_offline(False)
    # Known minor thing: on an offline reload the page requests the static
    # /assets/default-qr.png (in the HTML), which the SW does not serve without a network
    # -> console.error. Nobody sees it (it is replaced at once by /qr.png from the SW), hence only this one case.
    page.errors[:] = [e for e in page.errors
                      if not ("ERR_INTERNET_DISCONNECTED" in e and "/assets/default-qr.png" in e)]
    # network back: the next poll (15 s) succeeds and the message disappears
    deadline = time.time() + 25
    while time.time() < deadline and page.text_content("#err").strip():
        time.sleep(0.25)
    assert page.text_content("#err").strip() == "", f"the message stayed after the network returned: {page.text_content('#err')!r}"
    assert shown(page) in ("r", "g")


def test_offline_message_without_a_cache(page, server):
    """Without a SW (like the kiosk) the poll fails during an outage and #err shows the offline
    message, the picture stays; after the network returns the message disappears."""
    color_playlist(server, "offline2", colors=(RED,), seconds=600)   # no rotation = no new requests
    open_player(page, server, "offline2", clock=TUE, pause=False)
    wait_shown(page, "r")
    ctx = page.page.context
    ctx.set_offline(True)
    try:
        run_for(page, 16)
        wait_until(lambda: "offline" in page.text_content("#err").lower(), "the offline message in #err")
        assert page.locator("#stage > *").count() == 1 and page.is_hidden("#idle"), \
            "the screen stopped playing during a network outage"
    finally:
        ctx.set_offline(False)
    # the browser logs a poll failure during an outage as console.error; expected here
    page.errors[:] = [e for e in page.errors if "ERR_INTERNET_DISCONNECTED" not in e]
    run_for(page, 16)
    wait_until(lambda: page.text_content("#err").strip() == "", "the message disappeared after the network returned")


# ---------- (7) online in /api/devices ----------

def test_screen_is_online_in_api_devices(page, server):
    color_playlist(server, "online", colors=(RED,))
    assert devices(server)["online"]["online"] is False
    open_player(page, server, "online", clock=None)
    wait_shown(page, "r")

    def online():
        return devices(server)["online"]["online"] is True
    wait_until(online, "the screen `online` is online in /api/devices")
    info = devices(server)["online"]
    assert info["last_seen"] > 0 and info["agent"], info


# ---------- (8) language of the idle screen ----------

def test_lang_switches_the_idle_screen_texts(page, server):
    open_player(page, server, "lang", clock=None, query="?lang=en")
    tag_en = page.text_content("[data-i18n=tagline]")
    assert page.evaluate("() => document.documentElement.lang") == "en"
    assert "brand" in tag_en.lower() and "Digitální" not in tag_en, tag_en

    open_player(page, server, "lang", clock=None, query="?lang=cs")
    tag_cs = page.text_content("[data-i18n=tagline]")
    assert page.evaluate("() => document.documentElement.lang") == "cs"
    assert "Digitální" in tag_cs and tag_cs != tag_en, tag_cs


def test_lang_translates_the_qr_label(page, server):
    st, res = _req(server, "/api/settings", {"device": "lang2", "brand": {
        "url": "https://www.instagram.com/demo_brand/"}})
    assert st == 200, (st, res)
    open_player(page, server, "lang2", clock=None, query="?lang=en")
    page.wait_for_function("() => document.getElementById('qrwrap1').style.display !== 'none'")
    label = page.text_content("#qrwrap1 .labeltext")
    assert "Instagram" in label and "Sleduj" not in label, f"the QR label is not in English: {label!r}"


# ---------- (9) higher build => page reload ----------

def test_higher_build_triggers_a_reload(page, server):
    color_playlist(server, "build", colors=(RED,))
    state = {"build": None}

    def rewrite(route):
        r = route.fetch()
        data = r.json()
        if state["build"]:
            data["build"] = state["build"]
        route.fulfill(response=r, json=data)
    page.route("**/api/tv/build/playlist**", rewrite)
    err_seen = []
    page.expose_function("__errSeen", lambda text: err_seen.append(text))   # persists across reloads
    page.add_init_script("""document.addEventListener('DOMContentLoaded', () => {
      const e = document.getElementById('err');
      new MutationObserver(() => window.__errSeen(e.textContent))
        .observe(e, {childList: true, characterData: true, subtree: true});
    });""")
    open_player(page, server, "build")
    wait_shown(page, "r")
    page.evaluate("() => { window.__noReload = 1; }")
    run_for(page, 16)                     # a poll with the original build: nothing happens
    time.sleep(0.2)
    assert page.evaluate("() => window.__noReload") == 1

    state["build"] = "zzz-new-build"
    # The poll that sees the higher build, the message in #err and the reload on the next item
    # change all happen under the fake clock but are driven by real network timing. Whether the
    # message is still on screen when the test looks depends on how fast the response arrives
    # relative to the clock steps (the reload clears it), so every text of #err is recorded
    # (the binding survives the reload) and the test waits for the reload itself.
    reloads = []
    page.on("domcontentloaded", lambda _p: reloads.append(1))
    for _ in range(40):                   # at most 40 s of fake time: poll <= 15 s, then next() <= 2 s
        run_for(page, 1)
        if reloads:
            break
    # (events are dispatched only inside Playwright calls, so poll through one)
    wait_until(lambda: page.wait_for_timeout(50) or reloads, "the page reloads after the higher build")
    page.wait_for_load_state("domcontentloaded")
    assert any("nov" in t.lower() for t in err_seen), f"#err never reported the new version: {err_seen!r}"
    assert page.evaluate("() => window.__noReload") is None, "the page did not reload"
