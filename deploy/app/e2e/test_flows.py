"""Full user flows in a real Chromium: pairing, several screens, a client
from scratch, delete with undo, schedule, the error state and a server outage.

The shared helper functions are copied from test_admin_smoke.py (the e2e
directory is not a package, so importing between tests would be brittle).
Browser errors (pageerror, console.error) fail every test; expected network
errors (a simulated 500, a stopped server) are explicitly subtracted in the
given test.
"""
import json
import os
import re
import signal
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

import pytest

LANGS = ["cs", "en"]
NO_CODE = "······"
# sources of the items the player has just rendered (image = background-image of the layer, video = <video src>)
STAGE_SRCS = """[...document.querySelectorAll('#stage .item')].flatMap(l =>
    [l.style.backgroundImage, ...[...l.querySelectorAll('video')].map(v => v.currentSrc || v.src)])"""


# The player renders cached media from a blob: URL once the service worker controls the page,
# and whether that already happened depends on timing. Tests that identify the item by its file
# name therefore run the player without a service worker (same trick as test_player_smoke.py).
NO_SW = "(() => { try { delete Navigator.prototype.serviceWorker; } catch (e) {} })()"


def no_sw(page):
    page.add_init_script(NO_SW)
    return page


# ---------- helpers ----------

def png_bytes(w=64, h=48, rgb=(200, 30, 30)):
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def make_png(tmp_path, name, rgb=(200, 30, 30)):
    p = tmp_path / name
    p.write_bytes(png_bytes(rgb=rgb))
    return str(p)


def tiles(page):
    return page.locator("#grid .tile")


def names(page):
    return page.eval_on_selector_all("#grid .tile", "els => els.map(e => e.dataset.file)")


def api_files(server, device="default"):
    return [urllib.parse.unquote(i["url"].replace("/media/", ""))
            for i in server.playlist(device)["items"]]


def open_admin(page, server, lang="cs", device=None):
    url = f"{server.base}/admin?lang={lang}" + (f"&device={device}" if device else "")
    with page.expect_response(lambda r: "/playlist" in r.url and r.request.method == "GET"):
        page.goto(url)
    page.wait_for_function("() => document.getElementById('ver').textContent !== 'v…'")
    page.wait_for_load_state("networkidle")
    assert page.text_content("#msgList").strip() == "", page.text_content("#msgList")
    assert not page.is_visible("#loadRetry")
    assert page.evaluate("() => document.documentElement.lang") == lang
    return page


def upload(page, *paths):
    n = tiles(page).count()
    page.set_input_files("#file", list(paths))
    page.wait_for_function(
        "n => document.querySelectorAll('#grid .tile').length >= n", arg=n + len(paths), timeout=20000)
    page.wait_for_load_state("networkidle")
    assert tiles(page).count() == n + len(paths)


def open_section(page, key):
    if not page.locator(f"details.sec[data-sec={key}][open]").count():
        page.click(f"details.sec[data-sec={key}] > summary")
    assert page.locator(f"details.sec[data-sec={key}][open]").count() == 1


def open_plan(tile):
    if not tile.locator("details.plan[open]").count():
        tile.locator("details.plan > summary").click()
    assert tile.locator("details.plan[open]").count() == 1


def post(server, path, data, lang=None, auth=True):
    """POST JSON -> (status, json). HTTP errors are not raised."""
    headers = {"Content-Type": "application/json"}
    if auth:
        headers.update(server.auth)
    url = server.base + path + (f"?lang={lang}" if lang else "")
    req = urllib.request.Request(url, json.dumps(data).encode(), headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def new_screen(server, label):
    st, r = post(server, "/api/screens", {"label": label})
    assert st == 200 and r["ok"], r
    return r["device"]


def pair_code(server, client):
    st, r = post(server, "/api/pair/request", {"client": client}, auth=False)
    assert st == 200, r
    return r["code"]


def screen_with_photo(server, make_page, tmp_path, label, rgb):
    """A new screen with one photo uploaded through the admin; returns (device, file name)."""
    dev = new_screen(server, label)
    pg = make_page()
    open_admin(pg, server, "cs", device=dev)
    upload(pg, make_png(tmp_path, f"{dev}.png", rgb))
    files = api_files(server, dev)
    assert len(files) == 1
    pg.close()
    return dev, files[0]


def drop_net_errors(page, *needles):
    """Subtracts expected network console.error entries (e.g. a simulated 500, a refused connection)."""
    page.errors[:] = [e for e in page.errors
                      if not (e.startswith("console.error: Failed to load resource")
                              and (not needles or any(n in e for n in needles)))]


def toast_text(page):
    return page.text_content("#toast").strip()


# ---------- (1) pairing ----------

def test_pairing_a_new_screen_redirects_to_the_player(page, server):
    page.goto(server.base + "/connect")
    page.wait_for_function(f"() => document.getElementById('code').textContent !== '{NO_CODE}'")
    code = page.text_content("#code").strip()
    assert re.fullmatch(r"[A-Z2-9]{6}", code)

    st, r = post(server, "/api/pair/claim", {"code": code, "label": "New hall"})
    assert st == 200 and r["ok"], r
    dev = r["device"]
    assert dev != "default"
    # the status is polled every 3 s -> location.replace to /tv/<name>
    page.wait_for_url(re.compile(r"/tv/" + re.escape(dev) + r"/?$"), timeout=15000)
    page.wait_for_load_state("networkidle")
    assert dev in server.get_json("/api/devices")["devices"]
    page.wait_for_timeout(500)


def test_pairing_an_existing_screen_loads_the_playlist(make_page, server, tmp_path):
    dev, fname = screen_with_photo(server, make_page, tmp_path, "Reception", (10, 150, 40))
    page = no_sw(make_page())
    page.goto(server.base + "/connect")
    page.wait_for_function(f"() => document.getElementById('code').textContent !== '{NO_CODE}'")
    code = page.text_content("#code").strip()
    with page.expect_response(lambda r: f"/api/tv/{dev}/playlist" in r.url, timeout=20000):
        st, r = post(server, "/api/pair/claim", {"code": code, "device": dev})
        assert st == 200 and r["device"] == dev, r
    assert page.url.rstrip("/").endswith("/tv/" + dev)
    # the player renders the photo from its playlist
    page.wait_for_function(
        "n => " + STAGE_SRCS + ".some(x => x.includes(n))",
        arg=urllib.parse.quote(fname), timeout=15000)


@pytest.mark.parametrize("lang", LANGS)
def test_pairing_expired_and_used_code_gives_an_understandable_error(page, server, lang):
    expired = {"cs": "Kód už neplatí", "en": "The code has expired"}[lang]
    used = {"cs": "Kód už byl použit", "en": "The code has already been used"}[lang]

    # a non-existent/expired code (valid shape, the server does not know it)
    st, r = post(server, "/api/pair/claim", {"code": "ZZZZZZ", "label": "x"}, lang=lang)
    assert st == 404 and r["error"] == expired, r

    # a used code
    code = pair_code(server, "e2eclient-pairing-" + lang)
    assert post(server, "/api/pair/claim", {"code": code, "label": "One"}, lang=lang)[0] == 200
    st, r = post(server, "/api/pair/claim", {"code": code, "label": "Two"}, lang=lang)
    assert st == 409 and r["error"] == used, r

    # the same in the admin: a toast with the server error in the chosen language
    dev = new_screen(server, "Target")
    for code_, text in ((code, used), ("ZZZZZZ", expired)):
        page.goto(f"{server.base}/admin?lang={lang}&device={dev}&pair={code_}")
        page.wait_for_selector("#pairBox.on")
        assert page.text_content("#pairCode").strip() == code_
        page.click("#pairClaim")
        page.wait_for_function("() => document.getElementById('toast').classList.contains('show')")
        assert toast_text(page).endswith(text), toast_text(page)
        assert page.locator("#toast.bad").count() == 1
    drop_net_errors(page)  # 404/409 from fetch is reported in the console as "Failed to load resource"


# ---------- (2) several screens ----------

def test_two_screens_each_only_its_own_content(make_page, server, tmp_path):
    a, fa = screen_with_photo(server, make_page, tmp_path, "Screen A", (200, 30, 30))
    b, fb = screen_with_photo(server, make_page, tmp_path, "Screen B", (30, 30, 200))
    assert a != b and fa != fb
    assert api_files(server, a) == [fa] and api_files(server, b) == [fb]

    page = make_page()
    open_admin(page, server, "cs", device=a)
    assert names(page) == [fa]
    # switching via the picker in the admin
    with page.expect_navigation():
        page.select_option("#deviceSelect", b)
    page.wait_for_function("() => document.getElementById('ver').textContent !== 'v…'")
    page.wait_for_function("n => document.querySelectorAll('#grid .tile').length === n", arg=1)
    page.wait_for_load_state("networkidle")
    assert names(page) == [fb]
    assert page.text_content("#msgList").strip() == ""
    assert page.evaluate("() => new URLSearchParams(location.search).get('device')") == b

    # players: each only its own file
    for dev, mine, other in ((a, fa, fb), (b, fb, fa)):
        tv = no_sw(make_page())
        tv.goto(f"{server.base}/tv/{dev}")
        tv.wait_for_function(
            "n => " + STAGE_SRCS + ".some(x => x.includes(n))",
            arg=urllib.parse.quote(mine), timeout=15000)
        srcs = tv.evaluate("() => " + STAGE_SRCS)
        assert not any(urllib.parse.quote(other) in s for s in srcs), srcs


def test_screen_choice_is_remembered_after_reload(make_page, server, tmp_path):
    a, fa = screen_with_photo(server, make_page, tmp_path, "Remember A", (200, 30, 30))
    b, fb = screen_with_photo(server, make_page, tmp_path, "Remember B", (30, 200, 30))
    page = make_page()
    open_admin(page, server, "cs", device=a)
    with page.expect_navigation():
        page.select_option("#deviceSelect", b)
    page.wait_for_function("n => document.querySelectorAll('#grid .tile').length === n", arg=1)
    # again without ?device= -> the last chosen screen
    open_admin(page, server, "cs")
    assert page.input_value("#deviceSelect") == b
    assert names(page) == [fb]
    assert page.text_content("#msgList").strip() == ""


# ---------- (3) a client flow from scratch ----------

def test_client_flow_screen_photo_brand_player(page, server, make_page, tmp_path):
    open_admin(page, server, "cs")
    dialogs = []

    def on_dialog(d):
        dialogs.append(d.message)
        d.accept("Client Salon")
    page.on("dialog", on_dialog)
    with page.expect_navigation():
        page.click("#newDevice")
    page.wait_for_function("() => document.getElementById('ver').textContent !== 'v…'")
    page.wait_for_load_state("networkidle")
    assert len(dialogs) == 1
    dev = page.evaluate("() => new URLSearchParams(location.search).get('device')")
    assert dev and dev != "default"
    assert any("Client Salon" in o for o in
               page.eval_on_selector_all("#deviceSelect option", "o => o.map(x => x.textContent)"))

    upload(page, make_png(tmp_path, "client.png", (240, 200, 20)))
    assert tiles(page).count() == 1
    fname = names(page)[0]
    assert api_files(server, dev) == [fname]

    open_section(page, "brand")
    page.fill("#brName", "Client Salon")
    page.fill("#brEmail", "info@example.net")
    page.fill("#brPhone", "+420 111 222 333")
    page.fill("#brCity", "Prague")
    with page.expect_response(lambda r: "/api/settings" in r.url and r.request.method == "POST"):
        page.click("#brandSave")
    brand = server.playlist(dev)["settings"]["brand"]
    assert brand["name"] == "Client Salon" and brand["city"] == "Prague"

    tv = no_sw(make_page())
    tv.goto(f"{server.base}/tv/{dev}")
    tv.wait_for_function(
        "n => " + STAGE_SRCS + ".some(x => x.includes(n))",
        arg=urllib.parse.quote(fname), timeout=15000)
    # the footer is not visible over the photo, but it must carry the client's brand (not the default brand)
    tv.wait_for_function("() => document.getElementById('footrest').textContent.includes('Prague')", timeout=15000)
    assert tv.text_content("#footrest").strip() == "+420 111 222 333 · Prague"
    assert tv.text_content("#footmail").strip().startswith("info@example.net")
    assert "Your City" not in tv.text_content("#footrest")


# ---------- (4) delete with undo ----------

def test_delete_undo_keeps_the_item(page, server, tmp_path):
    open_admin(page, server, "cs")
    upload(page, make_png(tmp_path, "undo.png"))
    fname = names(page)[0]
    page.click("#grid .tile [data-del]")
    page.wait_for_function("() => document.querySelectorAll('#grid .tile').length === 0")
    assert "Smazáno" in toast_text(page)
    assert api_files(server) == [fname], "the server may delete only after the Undo grace period"
    page.click("#toast .toast-act")
    page.wait_for_function("() => document.querySelectorAll('#grid .tile').length === 1")
    assert "vrácena" in toast_text(page)
    assert names(page) == [fname]
    # the grace period passes, yet nothing is deleted (the timer was cancelled)
    page.wait_for_timeout(7000)
    assert api_files(server) == [fname]
    assert tiles(page).count() == 1
    assert page.text_content("#msgList").strip() == ""


def test_delete_without_undo_happens_after_the_grace_period(page, server, tmp_path):
    open_admin(page, server, "cs")
    upload(page, make_png(tmp_path, "gone.png"))
    page.click("#grid .tile [data-del]")
    page.wait_for_function("() => document.querySelectorAll('#grid .tile').length === 0")
    assert toast_text(page).startswith("Smazáno")
    t0 = time.time()
    assert len(api_files(server)) == 1
    deadline = t0 + 8
    while api_files(server) and time.time() < deadline:
        page.wait_for_timeout(250)
    assert api_files(server) == [], "the item was not removed from the server after ~6 s"
    assert time.time() - t0 >= 5, "deleted prematurely, before the Undo grace period ended"
    page.wait_for_function("() => document.getElementById('toast').textContent.includes('smazána')", timeout=5000)
    assert tiles(page).count() == 0
    assert page.text_content("#msgList").strip() == ""


# ---------- (5) schedule in the admin ----------

@pytest.mark.parametrize("lang", LANGS)
def test_schedule_via_ui_api_badge_clear(page, server, tmp_path, lang):
    open_admin(page, server, lang)
    upload(page, make_png(tmp_path, "plan.png"))
    tile = tiles(page).first
    open_plan(tile)
    tile.locator("[data-planbtn]").click()
    for d in (5, 6):
        tile.locator(f"[data-planday='{d}']").click()
    tile.locator("[data-planfrom]").fill("09:30")
    tile.locator("[data-planto]").fill("17:45")
    with page.expect_response(lambda r: "/api/item" in r.url and r.request.method == "POST") as ri:
        tile.locator("[data-plansave]").click()
    assert ri.value.status == 200
    it = server.playlist()["items"][0]
    assert it["schedule"]["from"] == "09:30" and it["schedule"]["to"] == "17:45"
    assert sorted(it["schedule"]["days"]) == [5, 6]
    badge = page.locator("#grid .tile .planbadge").first
    assert badge.is_visible()
    txt = badge.text_content()
    assert "09:30–17:45" in txt
    assert ("So, Ne" if lang == "cs" else "Sat, Sun") in txt

    # the badge survives a reload, clearing removes it on the server too
    open_admin(page, server, lang)
    assert page.locator("#grid .tile .planbadge").count() == 1
    tile = tiles(page).first
    open_plan(tile)
    tile.locator("[data-planbtn]").click()
    with page.expect_response(lambda r: "/api/item" in r.url and r.request.method == "POST"):
        tile.locator("[data-planclear]").click()
    page.wait_for_function("() => !document.querySelector('#grid .tile .planbadge')")
    assert not server.playlist()["items"][0].get("schedule")
    assert page.text_content("#msgList").strip() == ""


# ---------- (6) the list error state ----------

def test_error_500_shows_the_card_and_retry_reloads(make_page, server, tmp_path):
    dev, fname = screen_with_photo(server, make_page, tmp_path, "Faulty", (90, 90, 200))
    page = make_page()
    pattern = re.compile(rf"/api/tv/{re.escape(dev)}/playlist")
    page.route(pattern, lambda route: route.fulfill(
        status=500, content_type="application/json", body='{"error":"boom"}'))
    page.goto(f"{server.base}/admin?lang=cs&device={dev}")
    page.wait_for_selector("#loadErr", state="visible", timeout=10000)
    assert page.is_visible("#loadRetry")
    assert "nepodařilo načíst" in page.text_content("#loadErr")
    assert not page.is_visible("#empty"), "an error must not pose as an empty list"
    assert page.text_content("#msgList").strip() != ""
    assert tiles(page).count() == 0

    page.unroute(pattern)
    with page.expect_response(lambda r: f"/api/tv/{dev}/playlist" in r.url and r.status == 200):
        page.click("#loadRetry")
    page.wait_for_function("() => document.querySelectorAll('#grid .tile').length === 1")
    assert names(page) == [fname]
    assert not page.is_visible("#loadErr")
    assert page.text_content("#msgList").strip() == ""
    drop_net_errors(page, "/playlist")


# ---------- (7) server stopped midway ----------

def _stop_server(server):
    """Terminates exactly our child tvserver.py on the given port (PID only, no pkill)."""
    me = os.getpid()
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/stat") as f:
                ppid = int(f.read().rsplit(")", 1)[1].split()[1])
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().split(b"\0")
        except (OSError, ValueError, IndexError):
            continue
        if ppid == me and b"tvserver.py" in b" ".join(cmd) and str(server.port).encode() in cmd:
            os.kill(int(pid), signal.SIGTERM)
            deadline = time.time() + 5
            while time.time() < deadline:
                try:
                    urllib.request.urlopen(server.base + "/api/health", timeout=0.5).read()
                except OSError:
                    return
                time.sleep(0.1)
            raise AssertionError("the server still responds after SIGTERM")
    raise AssertionError("tvserver process not found")


def test_offline_admin_with_items_shows_an_error_and_the_items_stay(page, server, tmp_path):
    open_admin(page, server, "cs")
    upload(page, make_png(tmp_path, "offline.png"))
    _stop_server(server)
    page.evaluate("() => load()")            # the same function that the 8 s timer calls
    page.wait_for_function("() => document.getElementById('msgList').textContent.trim() !== ''", timeout=10000)
    assert "Server neodpovídá" in page.text_content("#msgList")
    assert tiles(page).count() == 1, "the items must not vanish during an outage"
    assert not page.is_visible("#empty")
    drop_net_errors(page)


def test_offline_admin_without_items_does_not_show_the_empty_state(page, server):
    open_admin(page, server, "cs")
    assert page.is_visible("#empty")
    _stop_server(server)
    page.evaluate("() => load()")
    page.wait_for_selector("#loadErr", state="visible", timeout=10000)
    assert "Server neodpovídá" in page.text_content("#msgList")
    assert not page.is_visible("#empty"), "an outage poses as an empty list"
    assert page.is_visible("#loadRetry")
    drop_net_errors(page)
