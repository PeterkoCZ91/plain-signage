"""Server robustness (audit A11, A12, A13, A15).

A11: file I/O for telemetry / file checks must not hold the global _lock.
A12: _file opens the file first and takes the size from fstat; a truncated file
     does not leave the client hanging.
A13: autoconvert does not block threads (503 + Retry-After) and guards disk space.
A15: tests do not write into the application directory (see conftest.py).
"""
import builtins
import http.client
import json
import os
import shutil
import threading
import time
import unittest
from unittest import mock

import video_convert
import tvserver
from test_http import AUTH, HttpBase
from test_stream_upload import CTYPE, MB, WEBM, body_of

SLOW = 1.0
REAL_OPEN = builtins.open


def slow_open_for(path, modes):
    """An open() that first waits SLOW seconds for the given path and modes."""
    def _open(file, mode="r", *a, **kw):
        if file == path and any(m in mode for m in modes):
            time.sleep(SLOW)
        return REAL_OPEN(file, mode, *a, **kw)
    return _open


class Bg(threading.Thread):
    """Background thread: runs a request and records the completion time."""
    def __init__(self, fn):
        super().__init__(daemon=True)
        self.fn, self.done_at, self.result = fn, None, None

    def run(self):
        self.result = self.fn()
        self.done_at = time.monotonic()


class NoLockDuringIoTest(HttpBase):
    """A11: a slow disk does not block other requests."""

    def setUp(self):
        super().setUp()
        tvserver._state = {"devices": {"tv1": {"version": 0, "items": []}}, "pairs": {}}
        tvserver._telemetry_pruned_at = 0
        tvserver._availability_cache = {}

    def _fast_request_finishes_first(self, slow_fn):
        slow = Bg(slow_fn)
        slow.start()
        time.sleep(0.25)                         # the slow request is already holding the disk
        t0 = time.monotonic()
        st, _, _ = self.req("GET", "/api/tv/jine/playlist", auth=False)
        fast_done = time.monotonic()
        self.assertEqual(st, 200)
        self.assertLess(fast_done - t0, SLOW / 2)
        self.assertIsNone(slow.done_at, "the slow request should not be finished earlier")
        slow.join(10)
        self.assertGreater(slow.done_at, fast_done)
        return slow.result

    def test_slow_telemetry_write_does_not_block_others(self):
        opener = slow_open_for(tvserver.TELEMETRY, ("a",))
        with mock.patch.object(tvserver, "open", opener, create=True):
            res = self._fast_request_finishes_first(
                lambda: self.req("GET", "/api/tv/tv1/playlist?heartbeat=1", auth=False))
        self.assertEqual(res[0], 200)
        with REAL_OPEN(tvserver.TELEMETRY, encoding="utf-8") as fh:
            self.assertEqual(json.loads(fh.readline())["device"], "tv1")

    def test_touch_device_does_not_hold_global_lock_during_write(self):
        opener = slow_open_for(tvserver.TELEMETRY, ("a",))
        with mock.patch.object(tvserver, "open", opener, create=True):
            t = Bg(lambda: tvserver.touch_device("tv1", {}))
            t.start()
            time.sleep(0.25)
            got = tvserver._lock.acquire(timeout=0.3)
            if got:
                tvserver._lock.release()
            t.join(10)
        self.assertTrue(got, "_lock stayed held during the telemetry write")

    def test_slow_availability_read_does_not_block_others(self):
        with REAL_OPEN(tvserver.TELEMETRY, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": int(time.time()) - 60, "device": "tv1"}) + "\n")
        opener = slow_open_for(tvserver.TELEMETRY, ("r",))
        with mock.patch.object(tvserver, "open", opener, create=True):
            res = self._fast_request_finishes_first(
                lambda: self.req("GET", "/api/devices/tv1/availability?hours=24"))
        self.assertEqual(res[0], 200)
        data = json.loads(res[2])
        self.assertEqual(data["events"], 1)
        self.assertIn("online", data)

    def test_slow_file_check_in_playlist_does_not_block_others(self):
        name = "pomaly.webm"
        with REAL_OPEN(os.path.join(tvserver.MEDIA, name), "wb") as fh:
            fh.write(WEBM)
        tvserver._state["devices"]["tv1"]["items"] = [
            {"id": name, "file": name, "type": "video", "seconds": 5}]
        real = os.path.isfile

        def slow_isfile(p):
            if str(p).endswith(name):
                time.sleep(SLOW)
            return real(p)
        with mock.patch.object(os.path, "isfile", slow_isfile):
            res = self._fast_request_finishes_first(
                lambda: self.req("GET", "/api/tv/tv1/playlist", auth=False))
        self.assertEqual(json.loads(res[2])["items"][0]["url"], "/media/" + name)

    def test_playlist_always_drops_vanished_files(self):
        tvserver._state["devices"]["tv1"]["items"] = [
            {"id": "x", "file": "chybi.webm", "type": "video", "seconds": 5}]
        st, _, body = self.req("GET", "/api/tv/tv1/playlist", auth=False)
        self.assertEqual(json.loads(body)["items"], [])
        self.assertEqual(tvserver._state["devices"]["tv1"]["items"], [])


class FileRaceTest(HttpBase):
    """A12."""

    def _media(self, name, data):
        with REAL_OPEN(os.path.join(tvserver.MEDIA, name), "wb") as fh:
            fh.write(data)

    def test_file_vanished_between_check_and_open_is_404(self):
        self._media("a.webm", WEBM * 10)

        def opener(file, mode="r", *a, **kw):
            if file.endswith("a.webm"):
                raise FileNotFoundError(file)
            return REAL_OPEN(file, mode, *a, **kw)
        with mock.patch.object(tvserver, "open", opener, create=True):
            st, _, _ = self.req("GET", "/media/a.webm", auth=False)
        self.assertEqual(st, 404)

    def test_size_from_fstat_of_open_file_not_getsize(self):
        data = os.urandom(5000)
        self._media("a.webm", data)
        with mock.patch.object(os.path, "getsize", side_effect=AssertionError("getsize")):
            st, hdr, body = self.req("GET", "/media/a.webm", auth=False)
            st2, hdr2, body2 = self.req("GET", "/media/a.webm",
                                        headers={"Range": "bytes=10-19"}, auth=False)
        self.assertEqual((st, body), (200, data))
        self.assertEqual((st2, body2), (206, data[10:20]))
        self.assertEqual(hdr2["Content-Range"], "bytes 10-19/5000")

    def test_head_and_416_unchanged(self):
        self._media("a.webm", b"x" * 100)
        st, hdr, body = self.req("HEAD", "/media/a.webm", auth=False)
        self.assertEqual((st, hdr["Content-Length"], body), (200, "100", b""))
        st, hdr, _ = self.req("GET", "/media/a.webm",
                              headers={"Range": "bytes=500-600"}, auth=False)
        self.assertEqual(st, 416)
        self.assertEqual(hdr["Content-Range"], "bytes */100")

    def test_truncated_file_closes_connection_instead_of_hanging(self):
        self._media("a.webm", b"y" * 20000)
        real_fstat = os.fstat

        def lying_fstat(fd):
            st = real_fstat(fd)
            vals = list(st)
            vals[6] = st.st_size + 100000        # the file has "shrunk" in the meantime
            return os.stat_result(vals)
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        t0 = time.monotonic()
        with mock.patch.object(tvserver.os, "fstat", lying_fstat):
            c.request("GET", "/media/a.webm")
            r = c.getresponse()
            with self.assertRaises(http.client.IncompleteRead):
                r.read()
        c.close()
        self.assertLess(time.monotonic() - t0, 2.5, "the client hung until the timeout")


class AutoconvertNonBlockingTest(HttpBase):
    """A13: mocked ffmpeg, no real conversion."""

    def upload(self):
        h, t = body_of("v.webm", WEBM + b"\x00" * 2048)
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        c.request("POST", "/api/upload?device=d1", body=h + t,
                  headers={"Authorization": AUTH, "Content-Type": CTYPE})
        r = c.getresponse()
        out = (r.status, dict(r.getheaders()), json.loads(r.read()))
        c.close()
        return out

    def _patches(self):
        return [mock.patch.object(tvserver, "_repack_reason", return_value="duvod"),
                mock.patch.object(video_convert, "FFMPEG", "/usr/bin/ffmpeg-mock"),
                mock.patch.object(tvserver.shutil, "disk_usage")]

    def test_busy_slots_return_503_retry_after_after_short_wait(self):
        slots = threading.Semaphore(0)
        with mock.patch.object(tvserver, "_repack_reason", return_value="duvod"), \
                mock.patch.object(video_convert, "FFMPEG", "/usr/bin/ffmpeg-mock"), \
                mock.patch.object(video_convert, "_slots", slots), \
                mock.patch.object(video_convert, "CONVERT_WAIT_S", 0.3), \
                mock.patch.object(tvserver.subprocess, "run") as run:
            t0 = time.monotonic()
            st, hdr, body = self.upload()
            elapsed = time.monotonic() - t0
        self.assertEqual(st, 503)
        self.assertEqual(hdr.get("Retry-After"), "5")
        self.assertEqual(body["code"], "server_busy")
        self.assertLess(elapsed, 3)
        run.assert_not_called()
        self.assertEqual(os.listdir(tvserver.MEDIA), [])

    def test_default_slot_wait_is_short(self):
        self.assertLessEqual(video_convert.CONVERT_WAIT_S, 10)

    def test_low_disk_space_returns_507_and_does_not_run_ffmpeg(self):
        usage = shutil.disk_usage(tvserver.MEDIA)._replace(free=1024)
        with mock.patch.object(tvserver, "_repack_reason", return_value="duvod"), \
                mock.patch.object(video_convert, "FFMPEG", "/usr/bin/ffmpeg-mock"), \
                mock.patch.object(tvserver.shutil, "disk_usage", return_value=usage), \
                mock.patch.object(tvserver.subprocess, "run") as run:
            st, _, body = self.upload()
        self.assertEqual(st, 507)
        self.assertEqual(body["code"], "file_save_failed")
        run.assert_not_called()
        self.assertEqual(os.listdir(tvserver.MEDIA), [])
        # the slot was returned
        self.assertTrue(video_convert._slots.acquire(timeout=0.5))
        video_convert._slots.release()

    def test_enough_space_conversion_runs(self):
        usage = shutil.disk_usage(tvserver.MEDIA)._replace(free=10 * 1024 * MB)
        with mock.patch.object(tvserver, "_repack_reason", return_value="duvod"), \
                mock.patch.object(video_convert, "FFMPEG", "/usr/bin/ffmpeg-mock"), \
                mock.patch.object(tvserver.shutil, "disk_usage", return_value=usage), \
                mock.patch.object(tvserver.subprocess, "run",
                                  side_effect=OSError("mock")) as run:
            st, _, body = self.upload()
        run.assert_called_once()                 # ffmpeg was tried, failure = original file
        self.assertEqual(st, 200, body)


if __name__ == "__main__":
    unittest.main()
