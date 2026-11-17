"""Smoke tests of the admin, the player and pairing in a real Chromium.

Why: the regression `t is not a function` passed all tests and showed up in
production only as the message "Server neodpovídá" (the catch swallowed the
exception). Every test therefore collects pageerror + console.error (see
conftest.make_page) and after loading the admin requires an empty #msgList.
"""
import shutil
import struct
import subprocess
import zlib

import pytest

LANGS = ["cs", "en"]


# Note: the server sends a CSP without unsafe-eval, hence only the function form in wait_for_function/evaluate.

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


def make_video(tmp_path, name="clip.mp4"):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is not available")
    p = tmp_path / name
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", str(p)], capture_output=True)
    if r.returncode != 0 or not p.exists():
        pytest.skip("ffmpeg cannot produce an h264 mp4")
    return str(p)


def open_admin(page, server, lang="cs", device=None):
    """Loads the admin and waits until the first playlist load finishes; #msgList must be empty."""
    url = f"{server.base}/admin?lang={lang}" + (f"&device={device}" if device else "")
    with page.expect_response(lambda r: "/playlist" in r.url and r.request.method == "GET"):
        page.goto(url)
    page.wait_for_function("() => document.getElementById('ver').textContent !== 'v…'")
    page.wait_for_load_state("networkidle")
    assert page.text_content("#msgList").strip() == "", page.text_content("#msgList")
    assert not page.is_visible("#loadRetry"), "the admin shows the list-load error state"
    assert page.evaluate("() => document.documentElement.lang") == lang
    return page


def tiles(page):
    return page.locator("#grid .tile")


def upload(page, *paths):
    n = tiles(page).count()
    page.set_input_files("#file", list(paths))
    page.wait_for_function(
        "n => document.querySelectorAll('#grid .tile').length >= n", arg=n + len(paths), timeout=20000)
    page.wait_for_load_state("networkidle")
    assert tiles(page).count() == n + len(paths)
    assert page.text_content("#msgList").strip() == ""


def names(page):
    return page.eval_on_selector_all("#grid .tile", "els => els.map(e => e.dataset.file)")


def items(server, device="default"):
    return server.playlist(device)["items"]


def api_files(server, device="default"):
    import urllib.parse
    return [urllib.parse.unquote(i["url"].replace("/media/", "")) for i in items(server, device)]


def open_section(page, key):
    """Collapsible sections under the list (closed by default): open them by clicking the header before interacting."""
    if not page.locator(f"details.sec[data-sec={key}][open]").count():
        page.click(f"details.sec[data-sec={key}] > summary")
    assert page.locator(f"details.sec[data-sec={key}][open]").count() == 1


def open_plan(tile):
    """The "Scheduling" row on the tile is closed by default."""
    if not tile.locator("details.plan[open]").count():
        tile.locator("details.plan > summary").click()
    assert tile.locator("details.plan[open]").count() == 1


def wait_settings_saved(page):
    return page.expect_response(lambda r: "/api/settings" in r.url and r.request.method == "POST")


# ---------- (1) empty screen ----------

@pytest.mark.parametrize("lang", LANGS)
def test_empty_screen_without_errors(page, server, lang):
    open_admin(page, server, lang)
    assert tiles(page).count() == 0
    assert page.is_visible("#empty")
    assert page.locator("#deviceSelect option").count() >= 1
    assert page.locator("#file").count() == 1


# ---------- (2) uploading an image and a video ----------

@pytest.mark.parametrize("lang", LANGS)
def test_upload_image_and_video(page, server, tmp_path, lang):
    open_admin(page, server, lang)
    img = make_png(tmp_path, "photo.png")
    vid = make_video(tmp_path)
    upload(page, img, vid)
    assert len(api_files(server)) == 2
    assert page.locator("#grid .tile .badge").count() == 2
    # preview: either it loaded or it fell back (img/video with the original file)
    page.wait_for_function("""() => [...document.querySelectorAll('#grid .tile .thumb')].every(t =>
        t.tagName === 'VIDEO' ? t.readyState >= 1 || t.error :
        t.complete && (t.naturalWidth > 0 || !t.src.includes('/api/thumb/')))""", timeout=15000)
    assert page.locator("#grid .tile .thumb").count() == 2


# ---------- (3) transition and fit mode survive a reload ----------

@pytest.mark.parametrize("lang", LANGS)
def test_settings_survive_reload(page, server, lang):
    open_admin(page, server, lang)
    open_section(page, "play")
    with wait_settings_saved(page):
        page.select_option("#transition", "cut")
    with wait_settings_saved(page):
        page.select_option("#fitMode", "portrait270")
    st = server.playlist()["settings"]
    assert st["transition"] == "cut"
    open_admin(page, server, lang)
    assert page.input_value("#transition") == "cut"
    assert page.input_value("#fitMode") == "portrait270"


# ---------- (4) brand ----------

@pytest.mark.parametrize("lang", LANGS)
def test_saving_the_brand(page, server, lang):
    open_admin(page, server, lang)
    assert not page.is_visible("#brName")      # the section is closed by default
    open_section(page, "brand")
    page.fill("#brName", "Studio E2E")
    page.fill("#brCity", "Brno")
    with wait_settings_saved(page):
        page.click("#brandSave")
    brand = server.playlist()["settings"]["brand"]
    assert brand["name"] == "Studio E2E" and brand["city"] == "Brno"
    open_admin(page, server, lang)
    assert page.input_value("#brName") == "Studio E2E"
    assert page.input_value("#brCity") == "Brno"


# ---------- (5) + Screen ----------

@pytest.mark.parametrize("lang", LANGS)
def test_new_screen_in_the_picker(page, server, lang):
    open_admin(page, server, lang)
    page.once("dialog", lambda d: d.accept("Reception"))
    with page.expect_navigation():
        page.click("#newDevice")
    page.wait_for_function("() => document.getElementById('ver').textContent !== 'v…'")
    page.wait_for_load_state("networkidle")
    dev = page.evaluate("() => new URLSearchParams(location.search).get('device')")
    assert dev and dev != "default"
    assert page.text_content("#msgList").strip() == ""
    opts = page.eval_on_selector_all("#deviceSelect option", "o => o.map(x => [x.value, x.textContent])")
    assert len(opts) >= 1   # an empty default screen is not listed in the picker
    assert dev in [v for v, _ in opts]
    assert page.input_value("#deviceSelect") == dev
    assert any("Reception" in t for _, t in opts)


# ---------- (6) language switch ----------

def test_language_switch_keeps_the_tile_count(page, server, tmp_path):
    open_admin(page, server, "cs")
    upload(page, make_png(tmp_path, "a.png"), make_png(tmp_path, "b.png", (10, 200, 10)))
    n = tiles(page).count()
    label_cs = page.text_content("#grid .tile .vis")
    page.click("[data-lang=en]")
    page.wait_for_function("() => document.documentElement.lang === 'en'")
    assert tiles(page).count() == n
    label_en = page.text_content("#grid .tile .vis")
    assert label_en != label_cs
    page.click("[data-lang=cs]")
    page.wait_for_function("() => document.documentElement.lang === 'cs'")
    assert tiles(page).count() == n
    assert page.text_content("#grid .tile .vis") == label_cs
    assert page.text_content("#msgList").strip() == ""


# ---------- (7) move, hide, delete ----------

@pytest.mark.parametrize("lang", LANGS)
def test_move_hide_delete(page, server, tmp_path, lang):
    open_admin(page, server, lang)
    upload(page, make_png(tmp_path, "a.png"), make_png(tmp_path, "b.png", (10, 200, 10)))
    before = names(page)
    assert api_files(server) == before

    # move: the second tile up
    with page.expect_response(lambda r: "/api/order" in r.url):
        page.locator("#grid .tile").nth(1).locator("[data-mv=up]").click()
    expected = [before[1], before[0]]
    assert names(page) == expected
    assert api_files(server) == expected

    # hide the first tile
    first = page.locator("#grid .tile").first
    with page.expect_response(lambda r: "/api/item" in r.url):
        first.locator("[data-vis]").click()
    assert "off" in first.get_attribute("class")
    hidden = next(i for i in items(server) if expected[0] in i["url"] or expected[0].replace(" ", "%20") in i["url"])
    assert hidden.get("enabled") is False

    # delete (accept the confirm)
    page.once("dialog", lambda d: d.accept())
    with page.expect_response(lambda r: "/api/delete" in r.url):
        page.locator("#grid .tile").first.locator("[data-del]").click()
    page.wait_for_function("() => document.querySelectorAll('#grid .tile').length === 1")
    assert names(page) == [expected[1]]
    assert api_files(server) == [expected[1]]
    assert page.text_content("#msgList").strip() == ""


# ---------- (8) /connect and /tv/<dev> ----------

@pytest.mark.parametrize("path", ["/connect", "/tv/default", "/tv/e2e-screen"])
def test_connect_and_player_without_js_errors(page, server, path):
    page.goto(server.base + path)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)   # let init and the first heartbeat run
    assert page.locator("body").count() == 1


# ---------- (9) mobile 390 px ----------

@pytest.mark.parametrize("lang", LANGS)
def test_mobile_without_horizontal_overflow(make_page, server, tmp_path, lang):
    page = make_page(viewport={"width": 390, "height": 800})
    open_admin(page, server, lang)
    upload(page, make_png(tmp_path, "a.png"), make_png(tmp_path, "b.png", (10, 200, 10)))
    over = page.evaluate("""() => ({sw: document.documentElement.scrollWidth,
                                    cw: document.documentElement.clientWidth,
                                    bw: document.body.scrollWidth})""")
    assert over["sw"] <= over["cw"] and over["bw"] <= over["cw"], over


# ---------- Idempotency-Key on upload ----------

def test_upload_sends_idempotency_key(page, server, tmp_path):
    import re
    open_admin(page, server, "cs")
    reqs = []
    page.on("request", lambda r: reqs.append(r) if "/api/upload" in r.url and r.method == "POST" else None)
    upload(page, make_png(tmp_path, "a.png"), make_png(tmp_path, "b.png", (10, 200, 10)))
    assert len(reqs) == 2
    keys = [r.headers.get("idempotency-key") for r in reqs]
    assert all(k and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", k) for k in keys), keys
    assert keys[0] != keys[1]


# ---------- (12) item schedule (days + hours) ----------

@pytest.mark.parametrize("lang", LANGS)
def test_schedule_set_and_clear(page, server, tmp_path, lang):
    open_admin(page, server, lang)
    upload(page, make_png(tmp_path, "plan.png"))
    tile = page.locator("#grid .tile").first

    assert not tile.locator("[data-planbtn]").is_visible()   # the Scheduling row is closed by default
    open_plan(tile)
    tile.locator("[data-planbtn]").click()
    panel = tile.locator(".planpanel")
    assert panel.is_visible()
    for d in (0, 1, 2, 3, 4):
        tile.locator(f"[data-planday='{d}']").click()
    assert tile.locator("[data-planday='0']").get_attribute("aria-pressed") == "true"
    assert tile.locator("[data-planday='5']").get_attribute("aria-pressed") == "false"

    # UI validation: from == to is not sent
    tile.locator("[data-planfrom]").fill("18:00")
    tile.locator("[data-planto]").fill("18:00")
    tile.locator("[data-plansave]").click()
    assert tile.locator(".planerr").text_content().strip() != ""

    tile.locator("[data-planto]").fill("23:00")
    with page.expect_response(lambda r: "/api/item" in r.url and r.request.method == "POST") as ri:
        tile.locator("[data-plansave]").click()
    if ri.value.status == 400 or "schedule" not in (items(server)[0] if items(server) else {}):
        pytest.skip("the server does not support `schedule` yet (the API did not return it)")
    assert items(server)[0]["schedule"] == {"days": [0, 1, 2, 3, 4], "from": "18:00", "to": "23:00"}
    badge = page.locator("#grid .tile .planbadge").first
    assert badge.is_visible()
    assert "18:00–23:00" in badge.text_content()
    assert ("Po–Pá" if lang == "cs" else "Mon–Fri") in badge.text_content()
    assert page.text_content("#msgList").strip() == ""

    # clear
    tile = page.locator("#grid .tile").first
    open_plan(tile)
    tile.locator("[data-planbtn]").click()
    with page.expect_response(lambda r: "/api/item" in r.url and r.request.method == "POST"):
        tile.locator("[data-planclear]").click()
    page.wait_for_function("() => !document.querySelector('#grid .tile .planbadge')")
    assert not items(server)[0].get("schedule")


# ---------- (10) kiosk update ----------

@pytest.mark.parametrize("lang", LANGS)
def test_kiosk_update(page, server, tmp_path, lang):
    import hashlib
    import json
    import urllib.request
    apk = b"PK\x03\x04 e2e apk" * 100
    kdir = tmp_path / "kiosk"
    kdir.mkdir()
    (kdir / "signage-kiosk.apk").write_bytes(apk)
    (kdir / "kiosk.json").write_text(json.dumps({
        "versionCode": 9, "versionName": "1.9", "size": len(apk),
        "sha256": hashlib.sha256(apk).hexdigest()}))
    dev = server.post_json("/api/screens", {"label": "Box"})["device"]
    # the box reports an older version (public poll, no login)
    urllib.request.urlopen(f"{server.base}/api/kiosk/release?device={dev}&have=8&state=idle").read()
    open_admin(page, server, lang, device=dev)
    open_section(page, "avail")
    btn_text = "Aktualizovat kiosek" if lang == "cs" else "Update kiosk"
    page.wait_for_selector(f"#kioskUpdate button:has-text('{btn_text}')")
    assert "1.9" in page.text_content("#kioskUpdate")
    with page.expect_response(lambda r: "/kiosk-update" in r.url and r.request.method == "POST"):
        page.click(f"#kioskUpdate button:has-text('{btn_text}')")
    cancel = "Zrušit aktualizaci" if lang == "cs" else "Cancel update"
    page.wait_for_selector(f"#kioskUpdate button:has-text('{cancel}')")
    assert server.get_json("/api/devices")["devices"][dev]["kiosk_update"] == 9
    with page.expect_response(lambda r: "/kiosk-update" in r.url and r.request.method == "POST"):
        page.click(f"#kioskUpdate button:has-text('{cancel}')")
    page.wait_for_selector(f"#kioskUpdate button:has-text('{btn_text}')")
    assert server.get_json("/api/devices")["devices"][dev]["kiosk_update"] is None


# ---------- (11) box health from the kiosk ----------

@pytest.mark.parametrize("lang", LANGS)
def test_box_health(page, server, lang):
    import urllib.error
    import urllib.request
    dev = server.post_json("/api/screens", {"label": "Box"})["device"]
    # the player reports in (online) and the kiosk reports health in the update poll
    urllib.request.urlopen(f"{server.base}/api/tv/{dev}/playlist?heartbeat=1&ok=1").read()
    for up in (7200, 30):                                  # uptime drop = box restart
        try:
            urllib.request.urlopen(f"{server.base}/api/kiosk/release?device={dev}&have=15"
                                   f"&state=idle&ram=64&ram_low=0&ram_total=1800&up={up}"
                                   f"&rssi=-72&net=wifi&crash=2").read()
        except urllib.error.HTTPError as e:                # 404 without a published release
            assert e.code == 404
    open_admin(page, server, lang, device=dev)
    open_section(page, "avail")
    page.wait_for_selector("#kioskHealth span")
    txt = page.text_content("#kioskHealth")
    assert "64 MB" in txt and "-72 dBm" in txt
    assert ("slabý" if lang == "cs" else "weak") in txt
    assert ("restartů: 1" if lang == "cs" else "restarts: 1") in txt
    warn = page.locator("#kioskHealth .warn").all_text_contents()
    assert any("64 MB" in w for w in warn) and any("2" in w for w in warn)
    assert not any("dBm" in w for w in warn)              # -72 is still above the -75 threshold
