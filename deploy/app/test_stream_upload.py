"""T6: upload does not hold the whole file in memory and cleans up temp files."""
import hashlib
import http.client
import io
import json
import os
import shutil
import subprocess
import time
import tracemalloc
import unittest
from unittest import mock

import video_convert
import tvserver
from multipart import FileTooLargeError, MultipartError, parse_multipart
from test_http import AUTH, HttpBase

BOUND = "XBOUNDX"
CTYPE = "multipart/form-data; boundary=" + BOUND
MB = 1024 * 1024
WEBM = b"\x1a\x45\xdf\xa3"


def body_of(filename, data, extra=b""):
    head = (b"--" + BOUND.encode() + b"\r\n"
            + ('Content-Disposition: form-data; name="f0"; filename="%s"\r\n'
               "Content-Type: application/octet-stream\r\n\r\n" % filename).encode())
    return head, data + b"\r\n--" + BOUND.encode() + b"--\r\n" + extra


def spool_files(d):
    return [n for n in os.listdir(d) if n.startswith(".signage-upload-")]


class SpoolParserTest(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.join(os.environ.get("TMPDIR", "/tmp"),
                                "test-spool-%d" % os.getpid())
        os.makedirs(self.dir, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def parse(self, body, fp=None, **kw):
        return parse_multipart(fp or io.BytesIO(body), CTYPE, len(body), 200 * MB,
                               spool_dir=self.dir, **kw)

    def test_file_goes_to_temp_file_with_same_content(self):
        data = os.urandom(300 * 1024)
        h, t = body_of("a.webm", data)
        parts = self.parse(h + t)
        p = parts[0]
        self.assertIsNotNone(p.path)
        self.assertEqual(os.path.dirname(p.path), self.dir)
        self.assertTrue(os.path.basename(p.path).startswith(".signage-upload-"))
        self.assertEqual(p.size, len(data))
        with open(p.path, "rb") as f:
            self.assertEqual(f.read(), data)
        self.assertEqual(p.content, data)         # backward compatibility

    def test_plain_field_stays_in_memory(self):
        body = (b"--XBOUNDX\r\nContent-Disposition: form-data; name=\"device\"\r\n\r\n"
                b"sal\r\n--XBOUNDX--\r\n")
        p = self.parse(body)[0]
        self.assertIsNone(p.path)
        self.assertEqual(p.text(), "sal")
        self.assertEqual(os.listdir(self.dir), [])

    def test_empty_file_creates_no_temp_file(self):
        h, t = body_of("a.webm", b"")
        p = self.parse(h + t)[0]
        self.assertEqual(p.size, 0)
        self.assertEqual(p.content, b"")
        self.assertEqual(os.listdir(self.dir), [])

    def test_file_over_limit_leaves_nothing(self):
        h, t = body_of("a.webm", b"x" * (2 * MB))
        with self.assertRaises(FileTooLargeError):
            self.parse(h + t, max_file_bytes=100 * 1024)
        self.assertEqual(os.listdir(self.dir), [])

    def test_file_exactly_at_limit_passes(self):
        h, t = body_of("a.webm", b"y" * 5000)
        self.assertEqual(self.parse(h + t, max_file_bytes=5000)[0].size, 5000)
        with self.assertRaises(FileTooLargeError):
            self.parse(h + t, max_file_bytes=4999)

    def test_truncated_body_leaves_nothing(self):
        h, t = body_of("a.webm", b"z" * MB)
        body = h + t
        cut = body[: len(body) // 2]
        with self.assertRaises(MultipartError):
            parse_multipart(io.BytesIO(cut), CTYPE, len(body), 200 * MB, spool_dir=self.dir)
        self.assertEqual(os.listdir(self.dir), [])

    def test_reader_error_leaves_nothing(self):
        h, t = body_of("a.webm", b"z" * MB)
        body = h + t

        class Boom(io.BytesIO):
            def read(self, n=-1):
                if self.tell() > 300 * 1024:
                    raise ConnectionResetError("reset")
                return super().read(n)

        with self.assertRaises(ConnectionResetError):
            self.parse(body, fp=Boom(body))
        self.assertEqual(os.listdir(self.dir), [])

    def test_bad_second_file_cleans_up_first_too(self):
        one = (b"--XBOUNDX\r\nContent-Disposition: form-data; name=\"a\"; filename=\"a.webm\""
               b"\r\n\r\n" + b"a" * 1000 + b"\r\n")
        two = (b"--XBOUNDX\r\nContent-Disposition: form-data; name=\"b\"; filename=\"b.webm\""
               b"\r\n\r\n" + b"b" * 1000 + b"\r\n")     # without the closing delimiter
        body = one + two
        with self.assertRaises(MultipartError):
            self.parse(body)
        self.assertEqual(os.listdir(self.dir), [])

    def test_large_form_field_has_cap(self):
        body = (b"--XBOUNDX\r\nContent-Disposition: form-data; name=\"x\"\r\n\r\n"
                + b"q" * (3 * MB) + b"\r\n--XBOUNDX--\r\n")
        with self.assertRaises(MultipartError):
            self.parse(body)

    def test_largest_buffer_held_at_once_is_small(self):
        data = os.urandom(50 * MB)
        h, t = body_of("a.webm", data)
        body = h + t
        fp = io.BytesIO(body)
        biggest = []

        class Rec:
            def read(self_inner, n=-1):
                chunk = fp.read(n)
                biggest.append(len(chunk))
                return chunk

        tracemalloc.start()
        try:
            p = parse_multipart(Rec(), CTYPE, len(body), 200 * MB, spool_dir=self.dir)[0]
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(p.size, len(data))
        self.assertLess(peak, 8 * MB)
        self.assertLessEqual(max(biggest), 1 * MB)


class UploadHttpTest(HttpBase):
    def setUp(self):
        super().setUp()
        for n in os.listdir(tvserver.MEDIA):
            p = os.path.join(tvserver.MEDIA, n)
            if os.path.isfile(p):
                os.unlink(p)

    def post(self, filename, data, timeout=60):
        h, t = body_of(filename, data)
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        c.request("POST", "/api/upload?device=d1", body=h + t,
                  headers={"Authorization": AUTH,
                           "Content-Type": CTYPE})
        r = c.getresponse()
        out = (r.status, json.loads(r.read() or b"{}"))
        c.close()
        return out

    def test_60mb_upload_does_not_hold_file_in_memory(self):
        data = WEBM + os.urandom(1024) * 1  # header for validation
        data = data + b"\x00" * (60 * MB)
        h, t = body_of("velky.webm", data)
        body = h + t                       # created BEFORE measuring
        tracemalloc.start()
        try:
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
            c.request("POST", "/api/upload?device=d1", body=body,
                      headers={"Authorization": AUTH, "Content-Type": CTYPE})
            r = c.getresponse()
            resp = json.loads(r.read())
            peak = tracemalloc.get_traced_memory()[1]
            c.close()
        finally:
            tracemalloc.stop()
        self.assertEqual(r.status, 200, resp)
        self.assertEqual(len(resp["saved"]), 1)
        out = os.path.join(tvserver.MEDIA, resp["saved"][0])
        self.assertEqual(os.path.getsize(out), len(data))
        with open(out, "rb") as f:
            self.assertEqual(hashlib.md5(f.read()).digest(), hashlib.md5(data).digest())
        self.assertEqual(spool_files(tvserver.MEDIA), [])
        self.assertLess(peak, 20 * MB, "allocation peak %.1f MB" % (peak / MB))

    def test_response_has_legacy_shape(self):
        st, resp = self.post("ok.webm", WEBM + b"\x00" * 100)
        self.assertEqual(st, 200)
        for k in ("ok", "saved", "errors", "warnings", "fixes"):
            self.assertIn(k, resp)
        self.assertTrue(resp["saved"][0].endswith("ok.webm"))

    def test_validation_error_cleans_up_temp_file(self):
        st, resp = self.post("falesny.png", b"tohle neni png" * 1000)
        self.assertEqual(st, 200)
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error_codes"], ["media_invalid"])
        self.assertEqual(os.listdir(tvserver.MEDIA), [])

    def test_empty_file_and_unsupported_extension_cleaned_up(self):
        st, resp = self.post("a.webm", b"")
        self.assertEqual(resp["error_codes"], ["empty_file"])
        st, resp = self.post("a.exe", b"MZ" * 1000)
        self.assertFalse(resp["ok"])
        self.assertEqual(os.listdir(tvserver.MEDIA), [])

    def test_quota_returns_error_and_leaves_clean(self):
        with mock.patch.object(tvserver, "_reserve_upload", return_value=(None, "quota")):
            st, resp = self.post("a.webm", WEBM + b"\x00" * 100)
        self.assertEqual(resp["error_codes"], ["storage_quota"])
        self.assertEqual(os.listdir(tvserver.MEDIA), [])
        self.assertEqual(tvserver._uploading_bytes, 0)

    def test_interrupted_upload_leaves_no_temp_file(self):
        h, t = body_of("useknuty.webm", WEBM + b"\x00" * (4 * MB))
        body = h + t
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.putrequest("POST", "/api/upload?device=d1")
        c.putheader("Authorization", AUTH)
        c.putheader("Content-Type", CTYPE)
        c.putheader("Content-Length", str(len(body)))
        c.endheaders()
        c.send(body[: 2 * MB])
        time.sleep(0.3)
        c.close()                                  # client vanished mid-body
        deadline = time.time() + 10
        while time.time() < deadline and os.listdir(tvserver.MEDIA):
            time.sleep(0.1)
        self.assertEqual(os.listdir(tvserver.MEDIA), [])
        self.assertEqual(tvserver._state["devices"].get("d1", {}).get("items", []), [])

    def test_file_over_limit_413_no_leftovers(self):
        h, t = body_of("a.webm", WEBM + b"\x00" * (2 * MB))
        with mock.patch.object(tvserver, "MAX_FILE_BYTES", 100 * 1024):
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            c.request("POST", "/api/upload?device=d1", body=h + t,
                      headers={"Authorization": AUTH, "Content-Type": CTYPE})
            r = c.getresponse()
            r.read()
            c.close()
        self.assertEqual(r.status, 413)
        self.assertEqual(os.listdir(tvserver.MEDIA), [])


@unittest.skipUnless(video_convert.available(), "ffmpeg is not on the machine")
class AutoconvertFileTest(HttpBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mov = os.path.join(cls.tmp, "vzorek.mov")
        subprocess.run(
            [video_convert.FFMPEG, "-nostdin", "-v", "error", "-y",
             "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=1",
             "-c:v", "libx264", "-profile:v", "baseline", "-pix_fmt", "yuv420p",
             "-f", "mov", cls.mov],
            stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=120)

    def test_mov_is_remuxed_from_file_and_nothing_remains(self):
        with open(self.mov, "rb") as f:
            data = f.read()
        h, t = body_of("vzorek.mov", data)
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        c.request("POST", "/api/upload?device=d1", body=h + t,
                  headers={"Authorization": AUTH, "Content-Type": CTYPE})
        resp = json.loads(c.getresponse().read())
        c.close()
        self.assertEqual(len(resp["saved"]), 1, resp)
        name = resp["saved"][0]
        self.assertTrue(name.endswith("vzorek.mp4"), name)
        self.assertTrue(resp["fixes"])
        self.assertEqual(os.listdir(tvserver.MEDIA), [name])
        with open(os.path.join(tvserver.MEDIA, name), "rb") as f:
            self.assertIsNone(tvserver.validate_media(name, f.read()))


if __name__ == "__main__":
    unittest.main()
