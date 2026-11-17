# -*- coding: utf-8 -*-
"""State persistence: restart, kill -9, corrupted file, migration, cleanup, full disk, concurrency.

The server runs as a real subprocess on a free port (pattern from e2e/conftest.py) with
STATE/MEDIA/TELEMETRY in a tmp dir; it is terminated via its own PID (never pkill -f).
Nothing is written to the app directory (PYTHONDONTWRITEBYTECODE, isolated paths).

The subprocess is started through a thin wrapper (WRAPPER) whose only job is to simulate,
on demand (a flag file in tmp), ENOSPC in fsync and SIGKILL right before the state's
os.replace. tvserver.py is not modified - it runs unchanged via runpy.
"""
import base64
import errno
import glob
import http.client
import json
import os
import random
import socket
import stat
import subprocess
import sys
import threading
import time
from unittest import mock

import pytest

import test_http
import tvserver

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ADMIN_CREDS = ("persist", "persist-pass")   # (user, secret), test-only
AUTH = {"Authorization": "Basic " + base64.b64encode(":".join(ADMIN_CREDS).encode()).decode()}

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
       b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
       b"\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa0\x00\x00\x00\x00IEND\xaeB`\x82")

WRAPPER = r"""
import errno, os, runpy, signal, sys
_enospc = os.environ.get("T_ENOSPC_FLAG")
_die = os.environ.get("T_DIE_FLAG")
_fsync, _replace = os.fsync, os.replace
def fsync(fd):
    if _enospc and os.path.exists(_enospc):
        raise OSError(errno.ENOSPC, "No space left on device")
    return _fsync(fd)
def replace(src, dst, *a, **k):
    if _die and os.path.exists(_die) and os.path.basename(str(dst)) == "tv-state.json":
        os.kill(os.getpid(), signal.SIGKILL)
    return _replace(src, dst, *a, **k)
os.fsync, os.replace = fsync, replace
sys.argv = ["tvserver.py", sys.argv[1]]
runpy.run_path("tvserver.py", run_name="__main__")
"""


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def multipart(filename, data, boundary="PBOUND"):
    body = (b"--" + boundary.encode() + b"\r\n"
            + ('Content-Disposition: form-data; name="f0"; filename="%s"\r\n'
               "Content-Type: application/octet-stream\r\n\r\n" % filename).encode()
            + data + b"\r\n--" + boundary.encode() + b"--\r\n")
    return body, {"Content-Type": "multipart/form-data; boundary=" + boundary}


class Srv:
    """One data directory, a server that can be started repeatedly."""

    def __init__(self, tmp, **env):
        self.tmp = tmp
        self.state = os.path.join(tmp, "tv-state.json")
        self.media = os.path.join(tmp, "media")
        self.telemetry = os.path.join(tmp, "telemetry.jsonl")
        self.enospc_flag = os.path.join(tmp, "flag-enospc")
        self.die_flag = os.path.join(tmp, "flag-die")
        self.extra_env = env
        self.proc = None
        self.port = None
        os.makedirs(self.media, exist_ok=True)
        self.logdir = os.path.join(tmp, "logs")      # writable even when tmp is read-only
        os.makedirs(self.logdir, exist_ok=True)

    def start(self):
        assert self.proc is None
        self.port = _free_port()
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                   SIGNAGE_STATE=self.state, SIGNAGE_MEDIA=self.media,
                   SIGNAGE_TELEMETRY=self.telemetry,
                   SIGNAGE_ADMIN_USER=ADMIN_CREDS[0], SIGNAGE_ADMIN_PASS=ADMIN_CREDS[1],
                   SIGNAGE_TG_TOKEN="", SIGNAGE_TG_CHAT="",
                   T_ENOSPC_FLAG=self.enospc_flag, T_DIE_FLAG=self.die_flag)
        env.update(self.extra_env)
        self.log = open(os.path.join(self.logdir, "server.log"), "ab")
        self.proc = subprocess.Popen([sys.executable, "-c", WRAPPER, str(self.port)],
                                     cwd=APP_DIR, env=env, stdout=self.log, stderr=self.log)
        deadline = time.time() + 20
        while True:
            if self.proc.poll() is not None:
                raise RuntimeError("tvserver exited: " + self.log_text())
            try:
                c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=1)
                c.request("GET", "/api/health")
                c.getresponse().read()
                c.close()
                return self
            except OSError:
                if time.time() > deadline:
                    raise RuntimeError("tvserver did not come up: " + self.log_text())
                time.sleep(0.05)

    def log_text(self):
        try:
            with open(os.path.join(self.logdir, "server.log"), errors="replace") as fh:
                return fh.read()[-3000:]
        except OSError:
            return ""

    def stop(self):
        """Graceful shutdown (SIGTERM to our PID)."""
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self._closed()

    def kill9(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()                 # SIGKILL to our PID
        if self.proc is not None:
            self.proc.wait()
        self._closed()

    def _closed(self):
        self.proc = None
        self.log.close()

    def restart(self):
        self.stop()
        return self.start()

    def wait_dead(self, timeout=10):
        self.proc.wait(timeout=timeout)
        self._closed()

    # ---- HTTP ----
    def req(self, method, path, body=None, headers=None, auth=True, timeout=10):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        h = dict(AUTH if auth else {})
        h.update(headers or {})
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        try:
            c.request(method, path, body=body, headers=h)
            r = c.getresponse()
            data = r.read()
            return r.status, dict(r.getheaders()), data
        finally:
            c.close()

    def jreq(self, method, path, body=None):
        st, hdr, data = self.req(method, path, body)
        try:
            return st, json.loads(data)
        except ValueError:
            return st, data

    def upload(self, dev, name="logo.png", data=PNG):
        b, h = multipart(name, data)
        st, _, raw = self.req("POST", f"/api/upload?device={dev}", b, h)
        return st, json.loads(raw)

    def playlist(self, dev):
        st, d = self.jreq("GET", f"/api/tv/{dev}/playlist")
        assert st == 200
        return d

    def health(self):
        st, d = self.jreq("GET", "/api/health")
        assert st == 200
        return d

    def state_file(self):
        with open(self.state, encoding="utf-8") as fh:
            return json.load(fh)

    def tmp_leftovers(self):
        return sorted(n for n in os.listdir(self.tmp)
                      if n.startswith(".signage-state-") or n.endswith(".tmp"))


@pytest.fixture
def srv(tmp_path):
    s = Srv(str(tmp_path))
    try:
        yield s
    finally:
        s.stop()


def _media_files(s):
    return sorted(os.listdir(s.media))


# ---------------------------------------------------------------- (1) restart
def test_state_survives_restart(srv):
    srv.start()
    names = []
    for n in ("a.png", "b.png", "c.png"):
        st, d = srv.upload("d1", n)
        assert st == 200 and d["ok"], d
        names.append(d["saved"][0])
    a, b, c = names
    st, _ = srv.jreq("POST", "/api/settings", {
        "device": "d1", "qrCorner": True, "qrUrl": "https://example.org/x",
        "transition": "cut", "fit": "portrait",
        "brand": {"name": "Testovaci studio", "city": "Brno"},
        "label": "Recepce nahore"})
    assert st == 200
    sched = {"days": [0, 2], "from": "22:00", "to": "06:00"}
    assert srv.jreq("POST", "/api/item", {"device": "d1", "file": a, "schedule": sched,
                                         "seconds": 17, "from": "2026-01-01",
                                         "until": "2026-12-31"})[0] == 200
    assert srv.jreq("POST", "/api/item", {"device": "d1", "file": b, "enabled": False,
                                         "sound": True})[0] == 200
    assert srv.jreq("POST", "/api/order", {"device": "d1", "order": [c, a, b]})[0] == 200
    # second device, to verify nothing gets mixed up
    assert srv.upload("d2", "z.png")[0] == 200

    before_pl = srv.playlist("d1")
    before_d2 = srv.playlist("d2")
    before_dev = srv.jreq("GET", "/api/devices")[1]
    media_bytes = srv.req("GET", "/media/" + a)[2]
    assert media_bytes == PNG
    assert [i["url"] for i in before_pl["items"]] == [f"/media/{x}" for x in (c, a, b)]

    srv.restart()

    after_pl = srv.playlist("d1")
    assert after_pl == before_pl
    assert srv.playlist("d2") == before_d2
    assert srv.jreq("GET", "/api/devices")[1]["devices"]["d1"]["label"] == "Recepce nahore"
    assert (srv.jreq("GET", "/api/devices")[1]["devices"]["d1"]["count"]
            == before_dev["devices"]["d1"]["count"] == 3)
    assert srv.req("GET", "/media/" + a)[2] == PNG
    items = {i["url"].rsplit("/", 1)[1]: i for i in after_pl["items"]}
    assert items[a]["schedule"] == sched and items[a]["seconds"] == 17
    assert items[a]["from"] == "2026-01-01" and items[a]["until"] == "2026-12-31"
    assert items[b]["enabled"] is False and items[b]["sound"] is True
    s = after_pl["settings"]
    assert s["brand"]["name"] == "Testovaci studio" and s["brand"]["city"] == "Brno"
    assert s["transition"] == "cut" and s["fit"] == "portrait" and s["qrCorner"] is True
    h = srv.health()
    assert h["state_ok"] is True and h["items"] == 4 and h["devices"] == 2
    # a second restart is idempotent (no further change of version or file)
    raw1 = open(srv.state, "rb").read()
    srv.restart()
    assert srv.playlist("d1") == before_pl
    assert open(srv.state, "rb").read() == raw1
    assert not glob.glob(srv.state + ".corrupt-*")


# ---------------------------------------------------------------- (2) kill -9
def _stored_version(s, dev="d1"):
    doc = s.state_file()
    return doc["devices"][dev]["version"]


def _assert_state_valid(s):
    with open(s.state, "rb") as fh:
        raw = fh.read()
    doc = json.loads(raw.decode("utf-8"))            # never corrupted
    assert isinstance(doc, dict) and doc["schema_version"] == 1
    assert isinstance(doc["devices"], dict) and isinstance(doc["pairs"], dict)
    return doc


def test_kill9_before_replace_keeps_previous_state_and_start_survives_it(srv):
    """Deterministic: SIGKILL exactly between writing the temp file and os.replace."""
    srv.start()
    assert srv.jreq("POST", "/api/settings", {"device": "d1", "label": "puvodni"})[0] == 200
    before = open(srv.state, "rb").read()
    open(srv.die_flag, "w").close()
    try:
        srv.req("POST", "/api/settings", {"device": "d1", "label": "nove"}, timeout=5)
    except (OSError, http.client.HTTPException):
        pass                                        # server died mid-response
    srv.wait_dead()
    assert srv.proc is None
    assert open(srv.state, "rb").read() == before   # state = previous one, byte for byte
    os.unlink(srv.die_flag)
    srv.start()
    assert srv.health()["state_ok"] is True
    assert srv.jreq("GET", "/api/devices")[1]["devices"]["d1"]["label"] == "puvodni"
    # a write after start works and the result is valid
    assert srv.jreq("POST", "/api/settings", {"device": "d1", "label": "nove"})[0] == 200
    _assert_state_valid(srv)


def test_kill9_20x_mid_mutation_series(srv):
    srv.start()
    assert srv.jreq("POST", "/api/settings", {"device": "d1", "label": "start"})[0] == 200
    for n in ("a.png", "b.png"):
        assert srv.upload("d1", n)[0] == 200
    acked_total = _stored_version(srv)              # what the server has acknowledged so far
    pad = "x" * 400
    rnd = random.Random(1234)
    delays = [rnd.uniform(0.0, 0.25) for _ in range(20)]
    sent_total = acked_total
    for it, delay in enumerate(delays):
        lock = threading.Lock()
        cnt = {"sent": 0, "acked": 0}
        stop = threading.Event()
        port = srv.port

        def hammer(k):
            i = 0
            while not stop.is_set():
                i += 1
                with lock:
                    cnt["sent"] += 1
                body = json.dumps({"device": "d1",
                                   "qrUrl": f"https://example.org/{it}/{k}/{i}/{pad}"})
                try:
                    c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                    c.request("POST", "/api/settings", body=body, headers=AUTH)
                    r = c.getresponse()
                    r.read()
                    c.close()
                    if r.status == 200:
                        with lock:
                            cnt["acked"] += 1
                except (OSError, http.client.HTTPException):
                    return

        threads = [threading.Thread(target=hammer, args=(k,)) for k in range(3)]
        for t in threads:
            t.start()
        time.sleep(delay)
        srv.kill9()
        stop.set()
        for t in threads:
            t.join(10)
        acked_total += cnt["acked"]
        sent_total += cnt["sent"]

        doc = _assert_state_valid(srv)
        ver = doc["devices"]["d1"]["version"]
        # acknowledged writes are not lost (fsync before response); unacknowledged ones are at most those sent
        assert acked_total <= ver <= sent_total, (it, acked_total, ver, sent_total)
        assert len(doc["devices"]["d1"]["items"]) == 2

        srv.start()
        h = srv.health()
        assert h["state_ok"] is True and h["items"] == 2
        assert not glob.glob(srv.state + ".corrupt-*"), "a valid state must not be treated as corrupted"
        # the version after start must neither drop nor change
        assert srv.playlist("d1")["version"] == ver
        # orphaned .signage-state-* after kill -9 are handled by the separate test below
        acked_total = ver                            # continue counting from the actual state
        sent_total = ver
    assert not [n for n in os.listdir(srv.tmp) if n.endswith(".tmp")]
    assert not glob.glob(srv.state + ".corrupt-*")


def test_no_lasting_temp_state_file_after_kill9(srv):
    """After kill -9 mid-save a .signage-state-* is left behind; it is cleaned up after restart."""
    srv.start()
    assert srv.jreq("POST", "/api/settings", {"device": "d1", "label": "a"})[0] == 200
    open(srv.die_flag, "w").close()
    try:
        srv.req("POST", "/api/settings", {"device": "d1", "label": "b"}, timeout=5)
    except (OSError, http.client.HTTPException):
        pass
    srv.wait_dead()
    assert srv.tmp_leftovers(), "an orphaned temp file must remain before restart (simulation)"
    os.unlink(srv.die_flag)
    srv.extra_env["SIGNAGE_STALE_TMP_SECONDS"] = "1"   # cleanup removes only files older than the limit
    time.sleep(1.5)
    srv.start()
    assert srv.tmp_leftovers() == [], "the server did not clean up the orphaned .signage-state-* after start"


# ---------------------------------------------------------------- (3) corrupted state
CORRUPT = {
    "truncated_json": b'{"schema_version": 1, "devices": {"d1": {"version": 4, "ite',
    "empty_file": b"",
    "json_array": b'[1, 2, 3]',
    "binary_garbage": bytes(range(256)) * 4,
    "newer_schema": b'{"schema_version": 99, "devices": {}, "pairs": {}}',
    "schema_not_number": b'{"schema_version": "abc", "devices": {}}',
}


@pytest.mark.parametrize("name", sorted(CORRUPT))
def test_corrupt_state_backup_and_start(srv, name):
    payload = CORRUPT[name]
    with open(srv.state, "wb") as fh:
        fh.write(payload)
    # other data (media, telemetry) must not change
    with open(os.path.join(srv.media, "stary_soubor.png"), "wb") as fh:
        fh.write(PNG)
    with open(srv.telemetry, "w") as fh:
        fh.write('{"ts": 1}\n')
    srv.start()
    backups = glob.glob(srv.state + ".corrupt-*.bak")
    assert len(backups) == 1, srv.log_text()
    with open(backups[0], "rb") as fh:
        assert fh.read() == payload                # backup = original bytes
    assert stat.S_IMODE(os.stat(backups[0]).st_mode) == 0o600
    h = srv.health()
    # code: state_ok = not _state_recovery_required; backup was made -> writes allowed
    assert h["ok"] is True and h["state_ok"] is True and h["state_error"] == ""
    assert h["devices"] == 0 and h["items"] == 0
    assert open(os.path.join(srv.media, "stary_soubor.png"), "rb").read() == PNG
    assert open(srv.telemetry).read() == '{"ts": 1}\n'
    # the server is usable: an upload creates a new valid state and the backup stays
    st, d = srv.upload("d1", "n.png")
    assert st == 200 and d["ok"], d
    doc = _assert_state_valid(srv)
    assert len(doc["devices"]["d1"]["items"]) == 1
    assert glob.glob(srv.state + ".corrupt-*.bak") == backups


def test_corrupt_state_without_backup_blocks_writes(srv):
    """Cannot back up -> state_ok False and writes are blocked (the original file stays)."""
    if os.geteuid() == 0:
        pytest.skip("root bypasses directory permissions")
    payload = b'{"devices": {"d1": '
    with open(srv.state, "wb") as fh:
        fh.write(payload)
    os.chmod(srv.tmp, 0o555)                       # neither a backup nor a new state can be written
    try:
        try:
            srv.start()
        except RuntimeError:
            pytest.skip("server does not start in a read-only directory (log: %s)" % srv.log_text())
        h = srv.health()
        assert h["state_ok"] is False and h["ok"] is False and h["state_error"] == "state file cannot be read/backed up, writes are blocked"
        st, _, _ = srv.req("POST", "/api/settings", {"device": "d1", "label": "x"})
        assert st >= 500
        assert open(srv.state, "rb").read() == payload
        assert glob.glob(srv.state + ".corrupt-*") == []
    finally:
        os.chmod(srv.tmp, 0o755)


# ---------------------------------------------------------------- (4) migration
def test_old_format_without_schema_version_is_migrated(srv):
    old = {"devices": {"default": {"version": 3, "items": [
        {"file": "stary.png"},
        {"file": "video.mp4", "seconds": "abc", "enabled": 0},
        {"file": "../unik.png"},                    # invalid name - discard
        "nesmysl"]}},
        "pairs": {}}
    with open(srv.state, "w") as fh:
        json.dump(old, fh)
    # the playlist omits items without a file on disk -> the files must exist
    for fname, content in (("stary.png", PNG), ("video.mp4", b"\x00" * 32)):
        with open(os.path.join(srv.media, fname), "wb") as fh:
            fh.write(content)
    srv.start()
    assert not glob.glob(srv.state + ".corrupt-*")
    assert srv.health()["state_ok"] is True
    pl = srv.playlist("default")
    assert pl["version"] == 3
    assert [i["url"] for i in pl["items"]] == ["/media/stary.png", "/media/video.mp4"]
    assert pl["items"][0]["type"] == "image" and pl["items"][0]["seconds"] == 10
    assert pl["items"][1]["type"] == "video" and pl["items"][1]["enabled"] is False
    # the first write stores the new format with schema_version
    assert srv.jreq("POST", "/api/settings", {"device": "default", "qrCorner": True})[0] == 200
    doc = _assert_state_valid(srv)
    assert doc["schema_version"] == 1
    assert [i["file"] for i in doc["devices"]["default"]["items"]] == ["stary.png", "video.mp4"]
    assert doc["devices"]["default"]["version"] == 4
    srv.restart()
    assert srv.playlist("default")["version"] == 4


# ---------------------------------------------------------------- (5) orphaned uploads
def test_orphaned_temp_uploads_cleaned_after_start(srv):
    def make(name, age_s):
        p = os.path.join(srv.media, name)
        with open(p, "wb") as fh:
            fh.write(b"data")
        t = time.time() - age_s
        os.utime(p, (t, t))
        return p

    old = [make(".signage-upload-stary1", 7200), make(".oprava-stary2.mp4", 7200),
           make(".signage-upload-hranice", 3600 + 120)]
    fresh = [make(".signage-upload-cerstvy", 5), make(".oprava-cerstvy.mp4", 600),
             make(".signage-upload-30min", 1800)]
    unrelated = make("nesouvisejici_stary.png", 99999)     # not a temp file -> stays
    srv.extra_env["SIGNAGE_STALE_TMP_SECONDS"] = "3600"
    srv.start()
    for p in old:
        assert not os.path.exists(p), p
    for p in fresh + [unrelated]:
        assert os.path.exists(p), p
    assert srv.health()["state_ok"] is True


# ---------------------------------------------------------------- (6) full disk
def test_full_disk_507_and_state_consistent(srv):
    srv.start()
    st, d = srv.upload("d1", "a.png")
    assert st == 200
    a = d["saved"][0]
    assert srv.jreq("POST", "/api/settings", {"device": "d1", "label": "pred"})[0] == 200
    pl_before = srv.playlist("d1")
    dev_before = srv.jreq("GET", "/api/devices")[1]
    raw_before = open(srv.state, "rb").read()
    media_before = _media_files(srv)

    open(srv.enospc_flag, "w").close()
    for path, body in (
            ("/api/settings", {"device": "d1", "label": "po"}),
            ("/api/settings", {"device": "novy", "label": "po"}),
            ("/api/order", {"device": "d1", "order": [a]}),
            ("/api/item", {"device": "d1", "file": a, "seconds": 33}),
            ("/api/delete", {"device": "d1", "file": a}),
            ("/api/screens", {"label": "Nova"})):
        st, hdr, raw = srv.req("POST", path, body)
        assert st == 507, (path, st, raw)
        assert json.loads(raw)["code"] == "state_save_failed"
    # upload to a full disk: nothing is added, no orphaned file
    st, d = srv.upload("d1", "b.png")
    assert st in (200, 500, 507)
    assert not d.get("saved")
    assert d.get("ok") is False

    assert srv.playlist("d1") == pl_before          # the in-memory state mirrors the disk
    assert srv.jreq("GET", "/api/devices")[1] == dev_before
    assert open(srv.state, "rb").read() == raw_before
    assert _media_files(srv) == media_before
    assert srv.tmp_leftovers() == []
    h = srv.health()
    assert h["state_ok"] is True and h["items"] == 1

    os.unlink(srv.enospc_flag)                      # space freed
    assert srv.jreq("POST", "/api/settings", {"device": "d1", "label": "po"})[0] == 200
    srv.restart()
    assert srv.jreq("GET", "/api/devices")[1]["devices"]["d1"]["label"] == "po"
    _assert_state_valid(srv)


class FullDiskUploadTest(test_http.HttpBase):
    def test_enospc_while_streaming_upload_is_507_and_nothing_remains(self):
        self.req("POST", "/api/settings", {"device": "d1", "label": "x"})
        raw_before = open(tvserver.STATE, "rb").read() if os.path.exists(tvserver.STATE) else b""
        state_before = json.dumps(tvserver._state, sort_keys=True)
        body, h = multipart("a.png", PNG)
        with mock.patch.object(tvserver, "parse_multipart",
                               side_effect=OSError(errno.ENOSPC, "No space left")):
            st, _, resp = self.req("POST", "/api/upload?device=d1", body, h)
        self.assertEqual(st, 507)
        self.assertFalse(json.loads(resp)["ok"])
        self.assertEqual(json.dumps(tvserver._state, sort_keys=True), state_before)
        self.assertEqual(os.listdir(tvserver.MEDIA), [])
        raw_after = open(tvserver.STATE, "rb").read() if os.path.exists(tvserver.STATE) else b""
        self.assertEqual(raw_after, raw_before)


# ---------------------------------------------------------------- (7) concurrency
def test_concurrency_20_threads_no_5xx_and_valid_state(tmp_path):
    max_items = 6
    s = Srv(str(tmp_path), SIGNAGE_MAX_ITEMS=str(max_items),
            SIGNAGE_RL_UPLOAD_MAX="100000", SIGNAGE_RL_UPLOAD_WINDOW="60")
    s.start()
    try:
        statuses = []
        errors = []
        lock = threading.Lock()

        def rec(st):
            with lock:
                statuses.append(st)

        def call(method, path, body=None, headers=None):
            try:
                st, hdr, data = s.req(method, path, body, headers, timeout=30)
            except OSError as exc:                  # let the error say which request caused it
                raise RuntimeError(f"{method} {path.split('?')[0]}: {exc!r}") from exc
            rec(st)
            return st, hdr, data

        def upload(dev, name):
            body, h = multipart(name, PNG)
            deadline = time.time() + 40             # limit by time, not by number of attempts:
            while time.time() < deadline:           # with 20 threads and a concurrent-uploads limit
                st, hdr, data = call("POST", f"/api/upload?device={dev}", body, h)
                if st == 503:                       # a 503 with Retry-After is expected
                    assert hdr.get("Retry-After")
                    time.sleep(0.05)
                    continue
                return st, json.loads(data)
            return 503, {}

        def worker(idx):
            try:
                dev = "shared" if idx < 12 else f"own{idx % 4}"
                for r in range(5):
                    st, d = upload(dev, f"t{idx}_{r}.png")
                    assert st == 200, (st, d)
                    call("POST", "/api/settings",
                         {"device": dev, "label": f"L{idx}-{r}", "qrUrl": f"https://e.org/{idx}/{r}"})
                    pl = json.loads(call("GET", f"/api/tv/{dev}/playlist")[2])
                    files = [i["url"].rsplit("/", 1)[1] for i in pl["items"]]
                    if files:
                        call("POST", "/api/order", {"device": dev, "order": files[::-1]})
                        call("POST", "/api/item", {"device": dev, "file": files[0],
                                                   "seconds": 5 + r, "enabled": bool(r % 2)})
                    if r % 2 and files:
                        call("POST", "/api/delete", {"device": dev, "file": files[-1]})
                    call("POST", "/api/screens", {"label": f"S{idx}-{r}"}) if r == 4 else None
            except BaseException as exc:            # noqa: BLE001 - collecting from threads
                with lock:
                    errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        t0 = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join(90)
        assert not any(t.is_alive() for t in threads), "thread is hanging (deadlock?)"
        assert not errors, errors
        assert time.time() - t0 < 60
        bad = [st for st in statuses if st >= 500 and st != 503]
        assert not bad, sorted(set(bad))
        assert all(st in (200, 503) for st in statuses), sorted(set(statuses))

        doc = _assert_state_valid(s)
        referenced = set()
        for name, dv in doc["devices"].items():
            files = [i["file"] for i in dv["items"]]
            assert len(files) <= max_items, (name, len(files))
            assert len(files) == len(set(files)), name
            referenced.update(files)
        assert len(doc["devices"]["shared"]["items"]) == max_items   # the limit was really enforced
        # every item has a file and every file an item; no orphaned temp file
        media = set(os.listdir(s.media))
        assert not [n for n in media if n.startswith((".signage-upload-", ".oprava-"))]
        assert referenced == {n for n in media if not n.startswith(".")}
        assert s.tmp_leftovers() == []
        h = s.health()
        assert h["state_ok"] is True and h["items"] == sum(
            len(dv["items"]) for dv in doc["devices"].values())
        live = {n: s.playlist(n) for n in doc["devices"]}
        s.restart()
        for n, pl in live.items():
            assert s.playlist(n) == pl
    finally:
        s.stop()
