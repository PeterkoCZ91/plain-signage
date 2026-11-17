"""Fixtures for the smoke tests in a real browser (Playwright + Chromium).

The server (tvserver.py) runs as a subprocess on a free port with isolated
state and media in tmp_path. Without playwright the whole directory is skipped.
"""
import base64
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER, PASSWORD = "e2e", "e2e-pass"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, port):
        self.port = port
        self.base = f"http://127.0.0.1:{port}"
        tok = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        self.auth = {"Authorization": "Basic " + tok}

    def get_json(self, path):
        req = urllib.request.Request(self.base + path, headers=self.auth)
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)

    def post_json(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                     headers={**self.auth, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)

    def playlist(self, device="default"):
        return self.get_json(f"/api/tv/{device}/playlist")


@pytest.fixture
def server(tmp_path):
    port = _free_port()
    env = dict(os.environ,
               SIGNAGE_STATE=str(tmp_path / "state.json"),
               SIGNAGE_MEDIA=str(tmp_path / "media"),
               SIGNAGE_TELEMETRY=str(tmp_path / "telemetry.jsonl"),
               SIGNAGE_KIOSK_DIR=str(tmp_path / "kiosk"),
               SIGNAGE_ADMIN_USER=USER, SIGNAGE_ADMIN_PASS=PASSWORD,
               SIGNAGE_TG_TOKEN="", SIGNAGE_TG_CHAT="")
    (tmp_path / "media").mkdir()
    log = open(tmp_path / "server.log", "wb")
    proc = subprocess.Popen([sys.executable, "tvserver.py", str(port)],
                            cwd=APP_DIR, env=env, stdout=log, stderr=log)
    srv = Server(port)
    try:
        deadline = time.time() + 20
        while True:
            if proc.poll() is not None:
                raise RuntimeError("tvserver exited: " + (tmp_path / "server.log").read_text())
            try:
                urllib.request.urlopen(srv.base + "/api/health", timeout=1).read()
                break
            except OSError:
                if time.time() > deadline:
                    raise RuntimeError("tvserver did not start within 20 s")
                time.sleep(0.1)
        yield srv
    finally:
        proc.terminate()          # only our PID, never pkill -f
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.close()


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # chromium is not installed
            pytest.skip(f"chromium is not available: {e}")
        yield b
        b.close()


class Page:
    """A page + captured errors; the test fails if any occurred."""

    def __init__(self, page):
        self.page = page
        self.errors = []
        page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        page.on("console", self._console)

    def _console(self, m):
        if m.type != "error":
            return
        url = m.location.get("url", "")
        # a 404 for the preview is an allowed branch: the admin falls back to the original
        if "/api/thumb/" in url and "Failed to load resource" in m.text:
            return
        self.errors.append(f"console.error: {m.text} ({url})")

    def __getattr__(self, name):
        return getattr(self.page, name)


@pytest.fixture
def make_page(browser, server):
    """make_page(viewport=None) -> a Page that captures errors (checked at teardown)."""
    contexts, pages = [], []

    def factory(viewport=None):
        # pinned: the player picks its language from the browser, and CI runs en-US
        kw = {"http_credentials": {"username": USER, "password": PASSWORD},
              "locale": "cs-CZ"}
        if viewport:
            kw["viewport"] = viewport
        ctx = browser.new_context(**kw)
        contexts.append(ctx)
        pg = Page(ctx.new_page())
        pages.append(pg)
        return pg

    yield factory
    errors = [e for pg in pages for e in pg.errors]
    for c in contexts:
        c.close()
    assert not errors, "browser errors:\n" + "\n".join(errors)


@pytest.fixture
def page(make_page):
    return make_page()
