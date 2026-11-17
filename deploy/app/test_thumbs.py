"""Media thumbnails in the admin: GET /api/thumb/<file>."""
import json
import os
import shutil
import subprocess
import unittest
from unittest import mock

import tvserver
from test_http import HttpBase

HAS_FFMPEG = bool(shutil.which("ffmpeg"))
JPEG = b"\xff\xd8\xff\xe0fakejpeg\xff\xd9"


def fake_make(src, dst, is_video):
    with open(dst, "wb") as f:
        f.write(JPEG)
    return True


class ThumbBase(HttpBase):
    def setUp(self):
        super().setUp()
        for n in os.listdir(tvserver.MEDIA):
            p = os.path.join(tvserver.MEDIA, n)
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p)
            else:
                os.remove(p)

    def put(self, name, data=b"x" * 100):
        p = os.path.join(tvserver.MEDIA, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def thumbs(self):
        d = os.path.join(tvserver.MEDIA, ".thumbs")
        return sorted(os.listdir(d)) if os.path.isdir(d) else []


class ThumbEndpointTest(ThumbBase):
    def test_without_password_401(self):
        self.put("a.png")
        st, _, _ = self.req("GET", "/api/thumb/a.png", auth=False)
        self.assertEqual(st, 401)

    def test_missing_file_404_json(self):
        st, h, body = self.req("GET", "/api/thumb/nic.png")
        self.assertEqual(st, 404)
        self.assertIn("error", json.loads(body))

    def test_path_traversal_404(self):
        self.put("a.png")
        for p in ("..%2Fstate.json", "%2e%2e%2fstate.json", "../a.png",
                  "sub%2F..%2Fa.png", "%2Fetc%2Fpasswd", ".thumbs"):
            with self.subTest(p=p):
                with mock.patch.object(tvserver, "_make_thumb", fake_make):
                    st, _, _ = self.req("GET", "/api/thumb/" + p)
                self.assertEqual(st, 404)

    def test_symlink_outside_root_404(self):
        outside = os.path.join(self.tmp, "venku.png")
        with open(outside, "wb") as f:
            f.write(b"x")
        os.symlink(outside, os.path.join(tvserver.MEDIA, "l.png"))
        with mock.patch.object(tvserver, "_make_thumb", fake_make):
            st, _, _ = self.req("GET", "/api/thumb/l.png")
        self.assertEqual(st, 404)
        self.assertEqual(self.thumbs(), [])

    def test_unknown_extension_404(self):
        self.put("a.txt")
        with mock.patch.object(tvserver, "_make_thumb", fake_make):
            st, _, _ = self.req("GET", "/api/thumb/a.txt")
        self.assertEqual(st, 404)

    def test_returns_jpeg_and_cache_hit_does_not_regenerate(self):
        self.put("a.png")
        calls = []

        def counting(src, dst, is_video):
            calls.append(is_video)
            return fake_make(src, dst, is_video)
        with mock.patch.object(tvserver, "_make_thumb", counting):
            st, h, body = self.req("GET", "/api/thumb/a.png")
            self.assertEqual(st, 200)
            self.assertEqual(h["Content-Type"], "image/jpeg")
            self.assertEqual(body, JPEG)
            st, _, body = self.req("GET", "/api/thumb/a.png")
            self.assertEqual((st, body), (200, JPEG))
        self.assertEqual(calls, [False])
        self.assertEqual(len(self.thumbs()), 1)

    def test_video_is_recognized(self):
        self.put("v.mp4")
        calls = []
        with mock.patch.object(tvserver, "_make_thumb",
                               lambda s, d, v: calls.append(v) or fake_make(s, d, v)):
            self.assertEqual(self.req("GET", "/api/thumb/v.mp4")[0], 200)
        self.assertEqual(calls, [True])

    def test_file_change_regenerates_and_deletes_old(self):
        p = self.put("a.png", b"x" * 10)
        with mock.patch.object(tvserver, "_make_thumb", fake_make):
            self.req("GET", "/api/thumb/a.png")
            self.put("a.png", b"y" * 20)
            os.utime(p, (1, 1))
            self.req("GET", "/api/thumb/a.png")
        self.assertEqual(len(self.thumbs()), 1)

    def test_ffmpeg_error_404_and_nothing_cached(self):
        self.put("a.png")
        with mock.patch.object(tvserver, "_make_thumb", lambda s, d, v: False):
            st, _, body = self.req("GET", "/api/thumb/a.png")
        self.assertEqual(st, 404)
        self.assertIn("error", json.loads(body))
        self.assertEqual(self.thumbs(), [])

    def test_without_ffmpeg_404(self):
        self.put("a.png")
        with mock.patch.object(tvserver.video_convert, "FFMPEG", None):
            st, _, _ = self.req("GET", "/api/thumb/a.png")
        self.assertEqual(st, 404)


class ThumbStorageTest(ThumbBase):
    def test_thumbs_do_not_count_toward_quota(self):
        self.put("a.png", b"x" * 100)
        with mock.patch.object(tvserver, "_make_thumb", fake_make):
            self.req("GET", "/api/thumb/a.png")
        self.assertEqual(len(self.thumbs()), 1)
        self.assertEqual(tvserver.storage_bytes(), 100)

    def test_thumbs_not_in_playlist(self):
        self.put("a.png")
        with mock.patch.object(tvserver, "_make_thumb", fake_make):
            self.req("GET", "/api/thumb/a.png")
        tvserver._state["devices"]["d1"] = {"version": 1, "items": []}
        st, _, body = self.req("GET", "/api/tv/d1/playlist", auth=False)
        self.assertEqual(json.loads(body)["items"], [])

    def test_delete_removes_thumb(self):
        self.put("a.png")
        tvserver._state["devices"]["d1"] = {"version": 1, "items": [
            {"file": "a.png", "type": "image", "seconds": 5}]}
        with mock.patch.object(tvserver, "_make_thumb", fake_make):
            self.req("GET", "/api/thumb/a.png")
            self.assertEqual(len(self.thumbs()), 1)
            st, _, _ = self.req("POST", "/api/delete", {"device": "d1", "file": "a.png"})
        self.assertEqual(st, 200)
        self.assertFalse(os.path.exists(os.path.join(tvserver.MEDIA, "a.png")))
        self.assertEqual(self.thumbs(), [])

    def test_cleanup_on_startup(self):
        self.put("zije.png")
        with mock.patch.object(tvserver, "_make_thumb", fake_make):
            self.req("GET", "/api/thumb/zije.png")
            p = self.put("mrtvy.png")
            self.req("GET", "/api/thumb/mrtvy.png")
        os.remove(p)
        self.assertEqual(len(self.thumbs()), 2)
        self.assertEqual(tvserver.cleanup_stale_thumbs(), 1)
        left = self.thumbs()
        self.assertEqual(len(left), 1)
        self.assertTrue(left[0].startswith("zije.png."))


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg missing")
class ThumbFfmpegTest(ThumbBase):
    def make_src(self, name, args):
        p = os.path.join(tvserver.MEDIA, name)
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args, p],
                       check=True, stdin=subprocess.DEVNULL)

    def size_of(self, body):
        dst = os.path.join(self.tmp, "t.jpg")
        with open(dst, "wb") as f:
            f.write(body)
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                              "stream=width,height", "-of", "csv=p=0", dst],
                             capture_output=True, text=True).stdout.strip()
        return tuple(int(x) for x in out.split(","))

    def test_image(self):
        self.make_src("big.png", ["-f", "lavfi", "-i", "testsrc=size=1600x900",
                                  "-frames:v", "1"])
        st, h, body = self.req("GET", "/api/thumb/big.png")
        self.assertEqual(st, 200)
        self.assertEqual(body[:2], b"\xff\xd8")
        if shutil.which("ffprobe"):
            self.assertEqual(max(self.size_of(body)), 320)

    def test_short_video_frame_from_zero(self):
        self.make_src("k.mp4", ["-f", "lavfi", "-i", "testsrc=size=640x360:rate=10:duration=0.5",
                                "-pix_fmt", "yuv420p"])
        st, _, body = self.req("GET", "/api/thumb/k.mp4")
        self.assertEqual(st, 200)
        self.assertEqual(body[:2], b"\xff\xd8")

    def test_long_video(self):
        self.make_src("d.mp4", ["-f", "lavfi", "-i", "testsrc=size=640x360:rate=10:duration=3",
                                "-pix_fmt", "yuv420p"])
        st, _, body = self.req("GET", "/api/thumb/d.mp4")
        self.assertEqual(st, 200)
        self.assertEqual(body[:2], b"\xff\xd8")

    def test_corrupt_file_404(self):
        self.put("bad.png", b"neni obrazek")
        st, _, _ = self.req("GET", "/api/thumb/bad.png")
        self.assertEqual(st, 404)
        self.assertEqual(self.thumbs(), [])


if __name__ == "__main__":
    unittest.main()
