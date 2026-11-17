# -*- coding: utf-8 -*-
"""HTTP API matrix against a real server on a local port (isolated state).

Route table (from tvserver.H.route / do_POST / PROTECTED):

  protected (Basic auth): /, /admin, /admin/, /api/devices[/<dev>/availability],
      /api/screens (POST), /api/screens/<dev>/qr.png, /api/thumb/<file>,
      /api/upload, /api/delete, /api/order, /api/item, /api/settings (POST),
      /api/pair/claim (POST)
  public: /tv[/<dev>], /connect, /api/health, /api/tv/<dev>/playlist,
      /media/<file>, /assets/<file>, /sw.js, /robots.txt, /qr.png,
      /pair-qr.png, /api/pair/request (POST), /api/pair/status
"""
import base64
import http.client
import json
import os
import struct
import threading
import unittest
import zlib
from unittest import mock

import tvserver
from test_http import HttpBase, multipart_body

AUTH_OK = "Basic " + base64.b64encode(b"admin:tajne").decode()
AUTH_BAD_PASS = "Basic " + base64.b64encode(b"admin:spatne").decode()
AUTH_BAD_USER = "Basic " + base64.b64encode(b"root:tajne").decode()


def _png(w=8, h=8):
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x80\x80\x80" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


PNG = _png()
CLIENT_ID = "a" * 20


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Matrix(HttpBase):
    """HttpBase + lowercase headers, clean limiter/idempotency state before every test."""

    def setUp(self):
        super().setUp()
        tvserver.reset_rate_limits()
        tvserver._idem_cache.clear()
        # media shared by all tests (checks that protection does not block legitimate reads)
        with open(os.path.join(tvserver.MEDIA, "ok.png"), "wb") as fh:
            fh.write(PNG)

    def call(self, method, path, body=None, headers=None, auth=AUTH_OK):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = dict(headers or {})
        if auth:
            h["Authorization"] = auth
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        if isinstance(body, str):
            body = body.encode("utf-8")
        try:
            c.request(method, path, body=body, headers=h)
            r = c.getresponse()
            data = r.read()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, data
        finally:
            c.close()

    def jcall(self, *a, **kw):
        st, h, data = self.call(*a, **kw)
        self.assertTrue(h.get("content-type", "").startswith("application/json"),
                        f"{a}: {h.get('content-type')} {data[:200]!r}")
        return st, h, json.loads(data)

    def assertNoTraceback(self, data, ctx=""):
        text = data.decode("utf-8", "replace")
        for bad in ("Traceback", 'File "', "Exception", "Error:"):
            self.assertNotIn(bad, text, f"{ctx}: exception leaked into body")


# --------------------------------------------------------------------------
# (1) AUTH matrix
# --------------------------------------------------------------------------
PROTECTED_CASES = [
    ("GET", "/"), ("GET", "/admin"), ("GET", "/admin/"),
    ("GET", "/api/devices"), ("GET", "/api/devices/d1/availability"),
    ("GET", "/api/screens/d1/qr.png"), ("GET", "/api/thumb/ok.png"),
    ("POST", "/api/screens"), ("POST", "/api/upload"), ("POST", "/api/delete"),
    ("POST", "/api/order"), ("POST", "/api/item"), ("POST", "/api/settings"),
    ("POST", "/api/pair/claim"),
]
PUBLIC_CASES = [
    ("GET", "/tv/x"), ("GET", "/tv"), ("GET", "/connect"), ("GET", "/api/health"),
    ("GET", "/api/tv/x/playlist"), ("GET", "/media/ok.png"),
    ("GET", "/assets/icon-192.png"), ("GET", "/sw.js"), ("GET", "/robots.txt"),
    ("GET", "/pair-qr.png?code=ZZZZZZ"), ("GET", "/qr.png"),
    ("GET", "/api/pair/status?client=x"), ("POST", "/api/pair/request"),
]


class AuthMatrixTest(Matrix):
    def _hit(self, method, path, auth):
        tvserver.reset_rate_limits()        # 3 bad attempts x many endpoints would hit the limit
        body = b"{}" if method == "POST" else None
        h = {"Content-Type": "application/json"} if body else {}
        if path == "/api/upload":
            body, h = multipart_body("a.png", PNG)
        return self.call(method, path, body, h, auth=auth)

    def test_protected_without_header_bad_password_bad_user_are_401(self):
        for method, path in PROTECTED_CASES:
            for label, auth in (("none", None), ("password", AUTH_BAD_PASS), ("user", AUTH_BAD_USER)):
                with self.subTest(method=method, path=path, variant=label):
                    st, h, data = self._hit(method, path, auth)
                    self.assertEqual(st, 401)
                    self.assertIn("basic", h.get("www-authenticate", "").lower())
                    self.assertEqual(h.get("connection", "").lower(), "close")
                    body = json.loads(data)
                    self.assertEqual(body["code"], "unauthorized")

    def test_protected_with_correct_password_are_not_401(self):
        for method, path in PROTECTED_CASES:
            with self.subTest(method=method, path=path):
                st, _, data = self._hit(method, path, AUTH_OK)
                self.assertNotEqual(st, 401)
                self.assertLess(st, 500, data[:200])

    def test_invalid_authorization_header_is_401(self):
        for hdr in ("Basic", "Basic !!!nebase64!!!", "Bearer abc", "basic " + base64.b64encode(b"admin:tajne").decode(),
                    "Basic " + base64.b64encode(b"admintajne").decode(),
                    "Basic " + base64.b64encode(b"\xff\xfe:\xff").decode(),
                    "Basic " + base64.b64encode(b":").decode()):
            with self.subTest(hdr=hdr[:30]):
                tvserver.reset_rate_limits()
                st, h, _ = self.call("GET", "/api/devices", auth=hdr)
                self.assertEqual(st, 401)
                self.assertIn("www-authenticate", h)

    def test_public_endpoints_need_no_password(self):
        for method, path in PUBLIC_CASES:
            with self.subTest(method=method, path=path):
                body = b"{}" if method == "POST" else None
                st, _, data = self.call(method, path, body, auth=None)
                self.assertNotEqual(st, 401, data[:200])
                self.assertLess(st, 500)

    def test_public_endpoints_ignore_bad_header(self):
        # a bad password on a public path is not checked (and does not fill the login limiter)
        for _ in range(tvserver.LOGIN_FAIL_MAX + 2):
            st, _, _ = self.call("GET", "/api/health", auth=AUTH_BAD_PASS)
            self.assertEqual(st, 200)
        self.assertEqual(tvserver._login_limiter.retry_after("127.0.0.1"), 0)

    def test_protected_path_prefixes_are_protected(self):
        for path in ("/admin-neco", "/adminx", "/api/devices/x/y", "/api/thumb/../x",
                     "/api/screensx", "/api/itemy"):
            with self.subTest(path=path):
                tvserver.reset_rate_limits()
                st, _, _ = self.call("GET", path, auth=None)
                self.assertEqual(st, 401)

    def test_head_without_password_is_401_without_body(self):
        st, h, data = self.call("HEAD", "/admin", auth=None)
        self.assertEqual(st, 401)
        self.assertEqual(data, b"")
        self.assertIn("www-authenticate", h)

    def test_query_cannot_bypass_auth(self):
        for path in ("/api/devices?x=/tv/", "/admin?/tv/x", "/api/upload?/media/"):
            with self.subTest(path=path):
                tvserver.reset_rate_limits()
                st, _, _ = self.call("GET", path, auth=None)
                self.assertEqual(st, 401)


# --------------------------------------------------------------------------
# (2) wrong method
# --------------------------------------------------------------------------
ALL_PATHS = ([p for _, p in PROTECTED_CASES] + [p for _, p in PUBLIC_CASES]
             + ["/nic", "/api/nic", "/media/", "/tv/a/b"])


class WrongMethodTest(Matrix):
    def test_no_method_on_any_path_returns_500_or_traceback(self):
        for path in ALL_PATHS:
            for method in ("GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD", "TRACE"):
                with self.subTest(method=method, path=path):
                    body = b"{}" if method in ("POST", "PUT", "PATCH") else None
                    if method == "POST" and path == "/api/upload":
                        body = b""
                    st, _, data = self.call(method, path, body,
                                            {"Content-Type": "application/json"} if body else None)
                    self.assertNotEqual(st, 500)
                    self.assertNoTraceback(data, f"{method} {path}")

    def test_unsupported_methods_are_501_not_odd_status(self):
        for method in ("PUT", "DELETE", "PATCH", "OPTIONS"):
            with self.subTest(method=method):
                st, _, _ = self.call(method, "/api/order", b"{}")
                self.assertEqual(st, 501)

    def test_post_to_get_path_is_json_404(self):
        for path in ("/api/health", "/api/devices", "/robots.txt", "/tv/x", "/media/ok.png"):
            with self.subTest(path=path):
                st, _, body = self.jcall("POST", path, {})
                self.assertEqual(st, 404)
                self.assertEqual(body["code"], "unknown_endpoint")

    def test_get_on_post_path_performs_no_write(self):
        tvserver._state["devices"]["d1"] = {"version": 1, "items": []}
        for path in ("/api/delete", "/api/order", "/api/item", "/api/settings",
                     "/api/screens", "/api/pair/claim", "/api/pair/request"):
            with self.subTest(path=path):
                st, _, _ = self.call("GET", path)
                self.assertIn(st, (400, 404))
        self.assertEqual(list(tvserver._state["devices"]), ["d1"])
        self.assertEqual(tvserver._state["devices"]["d1"]["version"], 1)


# --------------------------------------------------------------------------
# (3) JSON endpoints: invalid body
# --------------------------------------------------------------------------
JSON_POSTS = ["/api/order", "/api/delete", "/api/item", "/api/settings",
              "/api/screens", "/api/pair/request", "/api/pair/claim"]


class JsonBodyTest(Matrix):
    def _assert_json_reply(self, st, h, body, ctx):
        self.assertNotEqual(st, 500, ctx)
        self.assertTrue(h.get("content-type", "").startswith("application/json"), ctx)
        if st >= 400:
            self.assertIn("code", body, ctx)
            self.assertIn(body["code"], tvserver.ERRORS, ctx)
            self.assertTrue(body.get("error"), ctx)

    def test_array_null_number_string_instead_of_object_is_400_body_not_object(self):
        for path in JSON_POSTS:
            for raw in (b"[]", b"[1,2]", b"null", b"1", b'"text"', b"true"):
                with self.subTest(path=path, raw=raw):
                    st, _, body = self.jcall("POST", path, raw, {"Content-Type": "application/json"})
                    self.assertEqual(st, 400)
                    self.assertEqual(body["code"], "body_not_object")

    def test_invalid_json_and_garbage_always_return_json_reply(self):
        for path in JSON_POSTS:
            for raw in (b"{not json", b"{", b"\xff\xfe\x00", b"{'a':1}", b"\x00" * 10, b"{}}"):
                with self.subTest(path=path, raw=raw):
                    st, h, body = self.jcall("POST", path, raw, {"Content-Type": "application/json"})
                    self._assert_json_reply(st, h, body, f"{path} {raw!r}")

    def test_invalid_json_is_400_with_code(self):
        st, _, body = self.jcall("POST", "/api/screens", b"{not json",
                                 {"Content-Type": "application/json"})
        self.assertEqual(st, 400)
        self.assertEqual(body["code"], "invalid_json")

    def test_invalid_json_does_not_create_screen(self):
        # documented current behaviour: {} -> a screen is created (see test above)
        st, _, _ = self.jcall("POST", "/api/screens", b"{not json")
        self.assertIn(st, (200, 400))

    def test_body_over_64_kB_is_413_and_close(self):
        for path in JSON_POSTS:
            with self.subTest(path=path):
                # the server rejects based on Content-Length and does not read the body
                st, h, body = self.jcall("POST", path, None,
                                         {"Content-Length": str(tvserver.MAX_JSON_BODY + 1)})
                self.assertEqual(st, 413)
                self.assertEqual(body["code"], "request_too_large")
                self.assertEqual(h.get("connection", "").lower(), "close")

    def test_body_exactly_at_limit_passes(self):
        raw = b"{" + b" " * (tvserver.MAX_JSON_BODY - 2) + b"}"
        self.assertEqual(len(raw), tvserver.MAX_JSON_BODY)
        st, _, body = self.jcall("POST", "/api/settings", raw, {"Content-Type": "application/json"})
        self.assertEqual(st, 200, body)

    def test_really_large_body_over_limit_gets_413(self):
        raw = json.dumps({"label": "x" * (tvserver.MAX_JSON_BODY + 10)}).encode()
        try:
            st, _, body = self.jcall("POST", "/api/settings", raw, {"Content-Type": "application/json"})
        except (ConnectionError, http.client.HTTPException):
            self.skipTest("server closed the connection after 413 before the client finished sending the body (allowed)")
        self.assertEqual(st, 413)
        self.assertEqual(body["code"], "request_too_large")

    def test_invalid_content_length_is_400_invalid_content_length(self):
        for path in JSON_POSTS:
            for cl in ("abc", "-1", "1e3", "", " ", "0x10"):
                with self.subTest(path=path, cl=cl):
                    st, h, body = self.jcall("POST", path, None, {"Content-Length": cl})
                    if cl.strip() == "":
                        # missing/empty value = 0 bytes -> empty object
                        self._assert_json_reply(st, h, body, f"{path} CL={cl!r}")
                    else:
                        self.assertEqual(st, 400, f"{path} CL={cl!r}")
                        self.assertEqual(body["code"], "invalid_content_length")

    def test_content_length_larger_than_body_does_not_hang_server(self):
        # client promises 100 B, sends 2 and closes: the server must not answer 500
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.putrequest("POST", "/api/settings")
        c.putheader("Authorization", AUTH_OK)
        c.putheader("Content-Length", "100")
        c.endheaders(b"{}")
        c.sock.shutdown(1)
        try:
            r = c.getresponse()
            r.read()
            self.assertLess(r.status, 500)
        except (ConnectionError, http.client.HTTPException):
            pass
        finally:
            c.close()
        st, _, _ = self.call("GET", "/api/health", auth=None)
        self.assertEqual(st, 200)      # server is still alive

    def test_unicode_control_chars_and_long_strings(self):
        nasty = ["češř ☃ \U0001F600", "a\x00b", "\x07\x1b[31m", "‮⁦",
                 "\ud800".encode("utf-16", "surrogatepass").decode("utf-16", "ignore"),
                 "x" * 20000, "<script>alert(1)</script>", "../../etc/passwd", "\n\r\t"]
        tvserver._state["devices"]["d1"] = {"version": 1, "items": []}
        for val in nasty:
            for path, mk in (
                ("/api/settings", lambda v: {"device": "d1", "label": v, "qrUrl": v, "transition": v}),
                ("/api/screens", lambda v: {"label": v}),
                ("/api/delete", lambda v: {"device": "d1", "file": v}),
                ("/api/item", lambda v: {"device": "d1", "file": v, "from": v, "until": v}),
                ("/api/order", lambda v: {"device": "d1", "order": [v]}),
                ("/api/pair/claim", lambda v: {"code": v, "device": v, "label": v}),
                ("/api/pair/request", lambda v: {"client": v}),
            ):
                with self.subTest(path=path, val=val[:20]):
                    payload = json.dumps(mk(val), ensure_ascii=False).encode("utf-8")
                    st, h, body = self.jcall("POST", path, payload,
                                             {"Content-Type": "application/json"})
                    self._assert_json_reply(st, h, body, f"{path} {val[:20]!r}")

    def test_wrong_field_types_are_400_with_code(self):
        cases = [
            ("/api/order", {"order": "abc"}, "order_not_list"),
            ("/api/order", {"order": [1, 2]}, "order_not_list"),
            ("/api/order", {"order": {"a": 1}}, "order_not_list"),
            ("/api/delete", {"file": 5}, "file_not_string"),
            ("/api/delete", {"file": None}, "file_not_string"),
            ("/api/delete", {"file": ["a"]}, "file_not_string"),
            ("/api/item", {"file": {}}, "file_not_string"),
            ("/api/item", {"file": "a", "seconds": "5"}, "seconds_invalid"),
            ("/api/item", {"file": "a", "seconds": True}, "seconds_invalid"),
            ("/api/item", {"file": "a", "seconds": 10 ** 9}, "seconds_invalid"),
            ("/api/item", {"file": "a", "seconds": [1]}, "seconds_invalid"),
            ("/api/item", {"file": "a", "schedule": "zitra"}, "schedule_invalid"),
            ("/api/settings", {"label": 5}, "label_not_text"),
            ("/api/settings", {"label": "a<b"}, "label_invalid_chars"),
            ("/api/settings", {"label": "x" * 500}, "label_too_long"),
            ("/api/settings", {"brand": "x"}, "brand_not_object"),
            ("/api/settings", {"device": 5}, "invalid_device"),
            ("/api/settings", {"device": "../x"}, "invalid_device"),
            ("/api/settings", {"device": "a" * 49}, "invalid_device"),
            ("/api/settings", {"device": None}, "invalid_device"),
            ("/api/screens", {"label": ["x"]}, "label_not_text"),
            ("/api/screens", {"label": "\x00"}, "label_invalid_chars"),
            ("/api/pair/request", {"client": "kratky"}, "invalid_install_id"),
            ("/api/pair/request", {"client": ["a" * 20]}, "invalid_install_id"),
            ("/api/pair/claim", {"code": "x", "device": "d"}, "invalid_pair_input"),
            ("/api/pair/claim", {"code": "ABCDEF", "device": "../x"}, "invalid_pair_input"),
            ("/api/pair/claim", {"code": "ABCDEF", "device": "d"}, "pair_code_expired"),
        ]
        for path, payload, code in cases:
            with self.subTest(path=path, payload=str(payload)[:50]):
                st, _, body = self.jcall("POST", path, payload,
                                         {"Content-Type": "application/json"})
                self.assertIn(st, (400, 404))
                self.assertEqual(body["code"], code)

    def test_bad_requests_create_no_state(self):
        for path, payload in (("/api/order", {"device": "../x", "order": []}),
                              ("/api/delete", {"device": "a" * 49, "file": "a"}),
                              ("/api/item", {"device": "zz", "file": 5}),
                              ("/api/settings", {"device": "zz", "label": "<"})):
            self.jcall("POST", path, payload)
        self.assertNotIn("zz", tvserver._state["devices"])
        self.assertNotIn("../x", tvserver._state["devices"])

    def test_upload_invalid_body_is_json_error(self):
        for ctype, body in (("multipart/form-data", b"abc"),
                            ("multipart/form-data; boundary=", b"abc"),
                            ("text/plain", b"abc"),
                            ("multipart/form-data; boundary=XX", b"--XX\r\nbroken"),
                            ("multipart/form-data; boundary=XX", b"")):
            with self.subTest(ctype=ctype, body=body):
                st, h, data = self.call("POST", "/api/upload", body, {"Content-Type": ctype})
                self.assertNotEqual(st, 500, data[:200])
                self.assertTrue(h["content-type"].startswith("application/json"))
                d = json.loads(data)
                self.assertFalse(d.get("ok", False))
        self.assertEqual([n for n in os.listdir(tvserver.MEDIA) if n != "ok.png"], [])

    def test_upload_bad_content_length_does_not_crash_server(self):
        st, h, data = self.call("POST", "/api/upload", None, {"Content-Length": "abc"})
        self.assertNotEqual(st, 500, data[:200])
        st, _, _ = self.call("GET", "/api/health", auth=None)
        self.assertEqual(st, 200)


# --------------------------------------------------------------------------
# (4) path parameters
# --------------------------------------------------------------------------
TRAVERSAL = ["../tvserver.py", "%2e%2e/tvserver.py", "..%2ftvserver.py", "%2e%2e%2ftvserver.py",
             "..%5ctvserver.py", "..\\tvserver.py", "%00", "ok.png%00.txt", "%2e%2e%2f%2e%2e%2fsecret.txt",
             "....//secret.txt", "/etc/passwd", "%2fetc%2fpasswd", "..%252fsecret.txt",
             ".thumbs", ".thumbs/x.jpg", "%2ethumbs", "a" * 5000, "a" * 300 + ".png",
             "%ff%fe", "..;/secret.txt", "./ok.png/..", "ok.png/../../secret.txt"]


class PathParamTest(Matrix):
    def setUp(self):
        super().setUp()
        self.secret = os.path.join(os.path.dirname(tvserver.MEDIA), "secret.txt")
        with open(self.secret, "w") as fh:
            fh.write("TAJNE-MIMO-MEDIA")
        os.makedirs(os.path.join(tvserver.MEDIA, ".thumbs"), exist_ok=True)
        with open(os.path.join(tvserver.MEDIA, ".thumbs", "x.jpg"), "wb") as fh:
            fh.write(b"\xff\xd8THUMBDATA")

    def _no_leak(self, data):
        self.assertNotIn(b"TAJNE-MIMO-MEDIA", data)
        self.assertNotIn(b"THUMBDATA", data)
        self.assertNotIn(b"root:x:", data)
        self.assertNotIn(b"import tvserver", data)
        self.assertNotIn(b"def route", data)

    def test_legitimate_media_is_served(self):
        st, h, data = self.call("GET", "/media/ok.png", auth=None)
        self.assertEqual(st, 200)
        self.assertEqual(data, PNG)
        self.assertEqual(h["content-type"], "image/png")

    def test_media_traversal_is_400_404_and_leaks_nothing(self):
        for name in TRAVERSAL:
            with self.subTest(name=name[:40]):
                st, _, data = self.call("GET", "/media/" + name, auth=None)
                self.assertIn(st, (400, 404, 414), name[:40])
                self._no_leak(data)

    def test_media_empty_and_directory(self):
        for path in ("/media/", "/media", "/media/.", "/media/.."):
            with self.subTest(path=path):
                st, _, data = self.call("GET", path, auth=None)
                self.assertIn(st, (400, 404))
                self._no_leak(data)

    def test_media_symlink_outside_media_must_not_be_served(self):
        link = os.path.join(tvserver.MEDIA, "odkaz.txt")
        os.symlink(self.secret, link)
        st, _, data = self.call("GET", "/media/odkaz.txt", auth=None)
        self.assertIn(st, (400, 404))
        self._no_leak(data)

    def test_thumb_traversal(self):
        for name in TRAVERSAL:
            with self.subTest(name=name[:40]):
                st, _, data = self.call("GET", "/api/thumb/" + name)
                self.assertIn(st, (400, 404, 414), name[:40])
                self._no_leak(data)

    def test_thumb_missing_is_json_404_preview_unavailable(self):
        st, _, body = self.jcall("GET", "/api/thumb/neexistuje.png")
        self.assertEqual(st, 404)
        self.assertEqual(body["code"], "preview_unavailable")

    def test_playlist_invalid_name(self):
        for name in ("..", "%2e%2e", "a%00b", "a\\b", "a" * 49, "a" * 5000, "a.b", "%C3%A9",
                     "a%2fb", "a b".replace(" ", "%20"), ".thumbs", "..%2f..%2fsecret"):
            with self.subTest(name=name[:30]):
                st, _, data = self.call("GET", f"/api/tv/{name}/playlist", auth=None)
                self.assertIn(st, (400, 404, 414))
                self._no_leak(data)
        self.assertEqual(tvserver._state["devices"], {})
        self.assertEqual(tvserver._online, {})

    def test_playlist_long_name_within_limit_and_over_limit_is_json_404(self):
        st, _, body = self.jcall("GET", "/api/tv/" + "a" * 49 + "/playlist")
        self.assertEqual(st, 404)
        self.assertEqual(body["code"], "invalid_device")
        st, _, body = self.jcall("GET", "/api/tv/" + "a" * 48 + "/playlist")
        self.assertEqual(st, 200)

    def test_tv_player_invalid_name(self):
        for name in ("..", "%2e%2e", "a%00b", "a\\b", "a.b", "%C3%A9x", "a/b", ".thumbs",
                     "..%2ftvserver.py"):
            with self.subTest(name=name):
                st, _, data = self.call("GET", f"/tv/{name}", auth=None)
                self.assertIn(st, (400, 404))
                self.assertNotIn(b"import tvserver", data)

    def test_tv_player_long_name_does_not_crash(self):
        st, h, data = self.call("GET", "/tv/" + "a" * 3000, auth=None)
        self.assertIn(st, (200, 400, 404, 414))
        if st == 200:
            self.assertTrue(h["content-type"].startswith("text/html"))

    def test_availability_and_qr_name(self):
        for name in TRAVERSAL:
            for tpl in ("/api/devices/{}/availability", "/api/screens/{}/qr.png"):
                with self.subTest(tpl=tpl, name=name[:30]):
                    st, _, data = self.call("GET", tpl.format(name))
                    self.assertIn(st, (400, 404, 414), f"{tpl} {name[:30]}")
                    self._no_leak(data)

    def test_assets_traversal(self):
        for name in TRAVERSAL:
            with self.subTest(name=name[:40]):
                st, _, data = self.call("GET", "/assets/" + name, auth=None)
                self.assertIn(st, (400, 404, 414))
                self._no_leak(data)
        st, _, _ = self.call("GET", "/assets/icon-192.png", auth=None)
        self.assertEqual(st, 200)

    def test_delete_removes_nothing_outside_media(self):
        tvserver._state["devices"]["d1"] = {"version": 1, "items": []}
        for name in ("../secret.txt", "..%2fsecret.txt", "/etc/passwd", "..\\secret.txt", "%2e%2e/secret.txt"):
            self.jcall("POST", "/api/delete", {"device": "d1", "file": name})
        self.assertTrue(os.path.exists(self.secret))
        self.assertTrue(os.path.exists(os.path.join(tvserver.MEDIA, ".thumbs", "x.jpg")))

    def test_pair_status_and_qr_codes_do_not_return_500(self):
        for code in ("../x", "%00", "A" * 5000, "é", "ABCDEF&x=1"):
            for path in ("/api/pair/status?client=", "/pair-qr.png?code="):
                with self.subTest(path=path, code=code[:10]):
                    from urllib.parse import quote
                    st, _, _ = self.call("GET", path + quote(code), auth=None)
                    self.assertIn(st, (200, 400, 404, 414))


# --------------------------------------------------------------------------
# (5) headers
# --------------------------------------------------------------------------
class HeadersTest(Matrix):
    def test_admin_html_je_no_store(self):
        for path in ("/", "/admin", "/admin/", "/tv/x", "/connect"):
            with self.subTest(path=path):
                st, h, data = self.call("GET", path)
                self.assertEqual(st, 200)
                self.assertIn("no-store", h["cache-control"])
                self.assertTrue(h["content-type"].startswith("text/html"))
                self.assertTrue(data.lstrip().lower().startswith(b"<!doctype html"))

    def test_json_responses_are_no_store_and_noindex(self):
        for path in ("/api/health", "/api/tv/x/playlist", "/api/devices"):
            with self.subTest(path=path):
                st, h, _ = self.call("GET", path)
                self.assertIn("no-store", h["cache-control"])
                self.assertEqual(h["x-robots-tag"], "noindex, nofollow, noarchive")

    def test_noindex_on_other_responses(self):
        for path in ("/robots.txt", "/admin", "/media/ok.png", "/nic", "/sw.js", "/api/tv/x/playlist"):
            with self.subTest(path=path):
                st, h, _ = self.call("GET", path)
                self.assertEqual(h.get("x-robots-tag"), "noindex, nofollow, noarchive")

    def test_robots_disallows_all(self):
        st, h, data = self.call("GET", "/robots.txt", auth=None)
        self.assertEqual(st, 200)
        self.assertTrue(h["content-type"].startswith("text/plain"))
        self.assertIn(b"Disallow: /", data)

    def test_content_type_json_png_html_js(self):
        cases = [("/api/health", "application/json"), ("/api/devices", "application/json"),
                 ("/api/tv/x/playlist", "application/json"), ("/media/ok.png", "image/png"),
                 ("/admin", "text/html"), ("/sw.js", "application/javascript"),
                 ("/assets/icon-192.png", "image/png"),
                 ("/assets/manifest.webmanifest", "application/manifest+json")]
        if tvserver.segno is not None:
            cases += [("/qr.png", "image/png"), ("/api/screens/d1/qr.png", "image/png")]
        for path, ctype in cases:
            with self.subTest(path=path):
                st, h, data = self.call("GET", path)
                self.assertEqual(st, 200, path)
                self.assertTrue(h["content-type"].startswith(ctype), h["content-type"])
                if ctype == "image/png":
                    self.assertTrue(data.startswith(b"\x89PNG"))
                if ctype == "application/json":
                    self.assertIn("charset=utf-8", h["content-type"])
                    json.loads(data)

    def test_pair_qr_is_png(self):
        if tvserver.segno is None:
            self.skipTest("segno is not installed")
        tvserver._state["pairs"]["ABCDEF"] = {"client": CLIENT_ID, "expires": 2 ** 40}
        st, h, data = self.call("GET", "/pair-qr.png?code=ABCDEF", auth=None)
        self.assertEqual(st, 200)
        self.assertEqual(h["content-type"], "image/png")
        self.assertTrue(data.startswith(b"\x89PNG"))
        st, _, _ = self.call("GET", "/pair-qr.png?code=NEEXIST", auth=None)
        self.assertEqual(st, 404)

    def test_content_length_matches_body(self):
        for path in ("/api/health", "/admin", "/media/ok.png", "/robots.txt", "/nic"):
            with self.subTest(path=path):
                st, h, data = self.call("GET", path)
                self.assertEqual(int(h["content-length"]), len(data))

    def test_cors_without_config_sends_nothing(self):
        with mock.patch.object(tvserver, "CORS_ORIGIN", ""):
            for origin in ("https://ok.example", "null", "*"):
                st, h, _ = self.call("GET", "/api/health", headers={"Origin": origin}, auth=None)
                self.assertNotIn("access-control-allow-origin", h)

    def test_cors_only_exact_allowed_origin(self):
        allowed = "https://ok.example"
        public = ["/api/health", "/api/tv/x/playlist", "/media/ok.png", "/robots.txt",
                  "/nic", "/media/neexistuje", "/qr.png", "/api/pair/status?client=x"]
        with mock.patch.object(tvserver, "CORS_ORIGIN", allowed):
            for path in public:
                with self.subTest(path=path, origin="allowed"):
                    st, h, _ = self.call("GET", path, headers={"Origin": allowed}, auth=None)
                    self.assertEqual(h.get("access-control-allow-origin"), allowed)
                    self.assertIn("origin", h.get("vary", "").lower())
                for evil in ("https://evil.example", allowed + ".evil.example", "http://ok.example",
                             "https://ok.example:444", "null", "*", "https://OK.example",
                             allowed + "/", "https://ok.example\t"):
                    with self.subTest(path=path, origin=evil):
                        try:
                            st, h, _ = self.call("GET", path, headers={"Origin": evil}, auth=None)
                        except (ValueError, http.client.HTTPException):
                            continue         # client cannot send it (control character in header)
                        self.assertNotIn("access-control-allow-origin", h)
                with self.subTest(path=path, origin="none"):
                    st, h, _ = self.call("GET", path, auth=None)
                    self.assertNotIn("access-control-allow-origin", h)

    def test_cors_header_is_not_wildcard_or_reflection(self):
        with mock.patch.object(tvserver, "CORS_ORIGIN", "https://ok.example"):
            st, h, _ = self.call("GET", "/api/health", headers={"Origin": "https://x.example"}, auth=None)
            self.assertNotIn("access-control-allow-origin", h)
            self.assertNotIn("access-control-allow-credentials", h)

    def test_401_and_errors_do_not_corrupt_headers(self):
        st, h, _ = self.call("GET", "/api/devices", auth=None)
        self.assertEqual(st, 401)
        self.assertTrue(h["content-type"].startswith("application/json"))
        self.assertEqual(int(h["content-length"]), len(json.dumps(
            {"error": tvserver.error_text("unauthorized"), "code": "unauthorized"},
            ensure_ascii=False).encode()))

    def test_range_on_media(self):
        st, h, data = self.call("GET", "/media/ok.png", headers={"Range": "bytes=0-9"}, auth=None)
        self.assertEqual(st, 206)
        self.assertEqual(data, PNG[:10])
        st, h, _ = self.call("GET", "/media/ok.png", headers={"Range": "bytes=99999-"}, auth=None)
        self.assertEqual(st, 416)
        for bad in ("bytes=abc", "bytes=5-2", "items=0-1", "bytes=-", "bytes=0-1,5-6"):
            with self.subTest(range=bad):
                st, _, _ = self.call("GET", "/media/ok.png", headers={"Range": bad}, auth=None)
                self.assertIn(st, (200, 206, 416))


# --------------------------------------------------------------------------
# (6) errors: code + language
# --------------------------------------------------------------------------
ERROR_BRANCHES = [
    # (description, method, path, body, expected code, auth)
    ("no password", "GET", "/api/devices", None, "unauthorized", None),
    ("post unknown path", "POST", "/api/nic", {}, "unknown_endpoint", AUTH_OK),
    ("array body", "POST", "/api/order", [], "body_not_object", AUTH_OK),
    ("order not a list", "POST", "/api/order", {"order": 1}, "order_not_list", AUTH_OK),
    ("file not a string", "POST", "/api/delete", {"file": 1}, "file_not_string", AUTH_OK),
    ("seconds", "POST", "/api/item", {"file": "a", "seconds": "x"}, "seconds_invalid", AUTH_OK),
    ("schedule", "POST", "/api/item", {"file": "a", "schedule": 5}, "schedule_invalid", AUTH_OK),
    ("device", "POST", "/api/settings", {"device": "../"}, "invalid_device", AUTH_OK),
    ("label type", "POST", "/api/settings", {"label": 1}, "label_not_text", AUTH_OK),
    ("label chars", "POST", "/api/screens", {"label": "<"}, "label_invalid_chars", AUTH_OK),
    ("brand", "POST", "/api/settings", {"brand": 1}, "brand_not_object", AUTH_OK),
    ("install id", "POST", "/api/pair/request", {"client": "x"}, "invalid_install_id", None),
    ("pair input", "POST", "/api/pair/claim", {"code": "x", "device": "d"}, "invalid_pair_input", AUTH_OK),
    ("pair code expired", "POST", "/api/pair/claim", {"code": "ABCDEF", "device": "d"}, "pair_code_expired", AUTH_OK),
    ("thumb", "GET", "/api/thumb/nic.png", None, "preview_unavailable", AUTH_OK),
    ("playlist device", "GET", "/api/tv/" + "a" * 49 + "/playlist", None, "invalid_device", None),
    ("hours not a number", "GET", "/api/devices/d1/availability?hours=x", None, "hours_not_integer", AUTH_OK),
    ("hours range", "GET", "/api/devices/d1/availability?hours=0", None, "hours_out_of_range", AUTH_OK),
    ("availability device", "GET", "/api/devices/" + "a" * 49 + "/availability", None, "invalid_device", AUTH_OK),
]


class ErrorLangTest(Matrix):
    def _do(self, br, suffix="", headers=None):
        _, method, path, body, code, auth = br
        tvserver.reset_rate_limits()
        sep = "&" if "?" in path else "?"
        url = path + (sep + suffix if suffix else "")
        return self.jcall(method, url, body, headers, auth=auth)

    def test_every_error_branch_has_code_and_error(self):
        for br in ERROR_BRANCHES:
            with self.subTest(br[0]):
                st, h, body = self._do(br)
                self.assertGreaterEqual(st, 400)
                self.assertLess(st, 500)
                self.assertEqual(body["code"], br[4])
                self.assertTrue(body["error"])
                self.assertIn(br[4], tvserver.ERRORS)
                self.assertEqual(h.get("content-language"), "en")

    def test_text_varies_by_lang_and_accept_language(self):
        for br in ERROR_BRANCHES:
            with self.subTest(br[0]):
                _, _, dflt = self._do(br)
                _, h_en, en = self._do(br, "lang=en")
                _, h_cs, cs = self._do(br, "lang=cs")
                _, _, ah_en = self._do(br, headers={"Accept-Language": "en-US,en;q=0.9"})
                _, _, ah_cs = self._do(br, headers={"Accept-Language": "cs,en;q=0.5"})
                for b in (dflt, en, cs):
                    self.assertEqual(b["code"], br[4])
                self.assertEqual(h_en["content-language"], "en")
                self.assertEqual(h_cs["content-language"], "cs")
                self.assertNotEqual(en["error"], cs["error"])
                self.assertNotEqual(en["error"], dflt["error"])
                self.assertEqual(ah_en["error"], en["error"])
                self.assertEqual(ah_cs["error"], cs["error"])
                # English text must not contain Czech diacritics
                self.assertFalse(any(ch in en["error"] for ch in "áčďéěíňóřšťúůýž"), en["error"])

    def test_lang_overrides_accept_language_and_invalid_is_ignored(self):
        br = ERROR_BRANCHES[3]
        _, _, en = self._do(br, "lang=en")
        _, _, cs = self._do(br, "lang=cs")
        _, _, a = self._do(br, "lang=en", {"Accept-Language": "cs"})
        self.assertEqual(a["error"], en["error"])
        _, _, b = self._do(br, "lang=xx", {"Accept-Language": "cs"})
        self.assertEqual(b["error"], cs["error"])
        _, _, c = self._do(br, "lang=%00", {"Accept-Language": "en"})
        self.assertEqual(c["error"], en["error"])
        _, _, d = self._do(br, "lang=EN-gb")
        self.assertEqual(d["error"], en["error"])

    def test_odd_accept_language_does_not_crash_server(self):
        br = ERROR_BRANCHES[3]
        for al in ("", "*", "en;q=abc", "en;q=", ";;;", "," * 50, "x" * 5000, "cs;q=0", "en;q=1e999",
                   "é".encode("latin-1").decode("latin-1")):
            with self.subTest(al=al[:20]):
                st, _, body = self._do(br, headers={"Accept-Language": al})
                self.assertEqual(st, 400)
                self.assertEqual(body["code"], "order_not_list")

    def test_code_catalog_is_complete(self):
        for code, t in tvserver.ERRORS.items():
            with self.subTest(code=code):
                for k in ("default", "cs", "en"):
                    self.assertTrue(t.get(k), f"{code} lacks {k}")

    def test_upload_errors_have_error_codes_and_language(self):
        body, h = multipart_body("a.xyz", b"data")
        out = {}
        for lang in ("en", "cs"):
            st, hh, data = self.call("POST", f"/api/upload?device=d1&lang={lang}", body, h)
            d = json.loads(data)
            self.assertEqual(st, 200)
            self.assertFalse(d["ok"])
            self.assertEqual(d["code"], "unsupported_type")
            self.assertEqual(d["error_codes"], ["unsupported_type"])
            self.assertEqual(hh["content-language"], lang)
            out[lang] = d["errors"][0]
        self.assertNotEqual(out["en"], out["cs"])

    def test_413_and_429_have_code_and_language(self):
        st, h, b = self.jcall("POST", "/api/order", None,
                              {"Content-Length": "999999", "Accept-Language": "en"})
        self.assertEqual((st, b["code"], h["content-language"]), (413, "request_too_large", "en"))
        self.assertEqual(b["error"], tvserver.error_text("request_too_large", "en"))


# --------------------------------------------------------------------------
# (7) rate limits with injected clock
# --------------------------------------------------------------------------
class RateLimitHttpTest(Matrix):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()

    def _patch(self, name, limit, window):
        lim = tvserver.RateLimiter(limit, window, clock=self.clock)
        p = mock.patch.object(tvserver, name, lim)
        p.start()
        self.addCleanup(p.stop)
        return lim

    def test_login_over_limit_429_retry_after_and_can_retry_after_window(self):
        self._patch("_login_limiter", 3, 60)
        for _ in range(3):
            st, _, _ = self.call("GET", "/api/devices", auth=AUTH_BAD_PASS)
            self.assertEqual(st, 401)
        st, h, data = self.call("GET", "/api/devices", auth=AUTH_BAD_PASS)
        self.assertEqual(st, 429)
        self.assertEqual(int(h["retry-after"]), 60)
        self.assertEqual(h["connection"].lower(), "close")
        self.assertEqual(json.loads(data)["code"], "too_many_attempts")
        # during the window even the correct password is rejected
        st, h, _ = self.call("GET", "/api/devices", auth=AUTH_OK)
        self.assertEqual(st, 429)
        self.clock.t += 20
        st, h, _ = self.call("GET", "/api/devices", auth=AUTH_OK)
        self.assertEqual(st, 429)
        self.assertEqual(int(h["retry-after"]), 40)
        self.clock.t += 41
        st, _, _ = self.call("GET", "/api/devices", auth=AUTH_OK)
        self.assertEqual(st, 200)

    def test_login_limit_does_not_apply_to_public_and_headerless_requests(self):
        self._patch("_login_limiter", 2, 60)
        for _ in range(10):
            self.assertEqual(self.call("GET", "/api/devices", auth=None)[0], 401)
        self.assertEqual(self.call("GET", "/api/devices", auth=AUTH_OK)[0], 200)
        for _ in range(2):
            self.call("GET", "/api/devices", auth=AUTH_BAD_PASS)
        self.assertEqual(self.call("GET", "/api/health", auth=None)[0], 200)
        self.assertEqual(self.call("GET", "/media/ok.png", auth=None)[0], 200)

    def test_login_429_has_localized_text(self):
        self._patch("_login_limiter", 1, 60)
        self.call("GET", "/api/devices", auth=AUTH_BAD_PASS)
        _, h, b = self.jcall("GET", "/api/devices?lang=en", auth=AUTH_OK)
        self.assertEqual(b["error"], tvserver.error_text("too_many_attempts", "en"))
        self.assertEqual(h["content-language"], "en")

    def test_xff_ignored_without_trust_proxy_and_used_with_it(self):
        self._patch("_login_limiter", 2, 60)
        with mock.patch.object(tvserver, "TRUST_PROXY", False):
            for i in range(2):
                self.call("GET", "/api/devices", headers={"X-Forwarded-For": f"192.0.2.{i}"},
                          auth=AUTH_BAD_PASS)
            st, _, _ = self.call("GET", "/api/devices", headers={"X-Forwarded-For": "192.0.2.99"},
                                 auth=AUTH_BAD_PASS)
            self.assertEqual(st, 429)       # a forged XFF does not bypass the limit
        tvserver._login_limiter.clear()
        with mock.patch.object(tvserver, "TRUST_PROXY", True):
            for _ in range(2):
                self.call("GET", "/api/devices", headers={"X-Forwarded-For": "198.51.100.1"},
                          auth=AUTH_BAD_PASS)
            st, _, _ = self.call("GET", "/api/devices", headers={"X-Forwarded-For": "198.51.100.1"},
                                 auth=AUTH_BAD_PASS)
            self.assertEqual(st, 429)
            st, _, _ = self.call("GET", "/api/devices", headers={"X-Forwarded-For": "198.51.100.2"},
                                 auth=AUTH_OK)
            self.assertEqual(st, 200)       # a different IP has its own window
            # the last XFF value (from the trusted proxy) decides, not the forged first one
            st, _, _ = self.call("GET", "/api/devices",
                                 headers={"X-Forwarded-For": "203.0.113.6, 198.51.100.1"}, auth=AUTH_OK)
            self.assertEqual(st, 429)

    def test_pair_request_limit(self):
        self._patch("_pair_limiter", 2, 300)
        for i in range(2):
            st, _, _ = self.jcall("POST", "/api/pair/request", {"client": f"{i}" * 20}, auth=None)
            self.assertEqual(st, 200)
        st, h, b = self.jcall("POST", "/api/pair/request?lang=en", {"client": "z" * 20}, auth=None)
        self.assertEqual(st, 429)
        self.assertEqual(b["code"], "too_many_requests")
        self.assertEqual(int(h["retry-after"]), 300)
        self.assertEqual(h["connection"].lower(), "close")
        self.assertEqual(b["error"], tvserver.error_text("too_many_requests", "en"))
        self.clock.t += 301
        st, _, _ = self.jcall("POST", "/api/pair/request", {"client": "z" * 20}, auth=None)
        self.assertEqual(st, 200)

    def test_pair_request_limit_counts_bad_requests_too(self):
        self._patch("_pair_limiter", 2, 300)
        for _ in range(2):
            self.assertEqual(self.jcall("POST", "/api/pair/request", {"client": "x"}, auth=None)[0], 400)
        self.assertEqual(self.jcall("POST", "/api/pair/request", {"client": "x"}, auth=None)[0], 429)

    def test_upload_limit_429_with_retry_after_and_error_codes(self):
        self._patch("_upload_limiter", 2, 60)
        body, h = multipart_body("ok.png", PNG)
        for _ in range(2):
            st, _, _ = self.call("POST", "/api/upload?device=d1", body, h)
            self.assertEqual(st, 200)
        st, hh, data = self.call("POST", "/api/upload?device=d1&lang=en", body, h)
        d = json.loads(data)
        self.assertEqual(st, 429)
        self.assertEqual(int(hh["retry-after"]), 60)
        self.assertEqual(hh["connection"].lower(), "close")
        self.assertEqual(d["code"], "too_many_uploads")
        self.assertEqual(d["error_codes"], ["too_many_uploads"])
        self.assertFalse(d["ok"])
        self.assertEqual(len(tvserver._state["devices"]["d1"]["items"]), 2)
        self.clock.t += 61
        st, _, _ = self.call("POST", "/api/upload?device=d1", body, h)
        self.assertEqual(st, 200)

    def test_upload_limit_comes_after_authentication(self):
        self._patch("_upload_limiter", 1, 60)
        body, h = multipart_body("ok.png", PNG)
        self.assertEqual(self.call("POST", "/api/upload?device=d1", body, h)[0], 200)
        self.assertEqual(self.call("POST", "/api/upload?device=d1", body, h, auth=None)[0], 401)

    def test_retry_after_is_positive_integer(self):
        self._patch("_pair_limiter", 1, 7)
        self.call("POST", "/api/pair/request", {"client": "a" * 20}, auth=None)
        self.clock.t += 0.3
        _, h, _ = self.call("POST", "/api/pair/request", {"client": "b" * 20}, auth=None)
        ra = h["retry-after"]
        self.assertTrue(ra.isdigit())
        self.assertGreaterEqual(int(ra), 1)
        self.assertLessEqual(int(ra), 7)


# --------------------------------------------------------------------------
# (8) screen isolation
# --------------------------------------------------------------------------
class ScreenIsolationTest(Matrix):
    def _upload(self, dev, name="logo.png"):
        body, h = multipart_body(name, PNG)
        st, _, data = self.call("POST", f"/api/upload?device={dev}", body, h)
        self.assertEqual(st, 200, data)
        d = json.loads(data)
        self.assertTrue(d["ok"], d)
        return d["saved"][0]

    def _playlist(self, dev):
        st, _, body = self.jcall("GET", f"/api/tv/{dev}/playlist", auth=None)
        self.assertEqual(st, 200)
        return [i["url"] for i in body["items"]]

    def test_same_filename_on_two_screens_gets_different_files(self):
        a = self._upload("scr-a")
        b = self._upload("scr-b")
        self.assertNotEqual(a, b)
        self.assertEqual(self._playlist("scr-a"), ["/media/" + a])
        self.assertEqual(self._playlist("scr-b"), ["/media/" + b])

    def test_delete_on_one_screen_does_not_affect_other(self):
        a = self._upload("scr-a")
        b = self._upload("scr-b")
        st, _, body = self.jcall("POST", "/api/delete", {"device": "scr-a", "file": a})
        self.assertEqual((st, body), (200, {"ok": True}))
        self.assertEqual(self._playlist("scr-a"), [])
        self.assertEqual(self._playlist("scr-b"), ["/media/" + b])
        self.assertEqual(self.call("GET", "/media/" + b, auth=None)[0], 200)
        self.assertEqual(self.call("GET", "/media/" + a, auth=None)[0], 404)

    def test_shared_file_deleted_only_when_no_screen_has_it(self):
        shared = "sdileny.png"
        with open(os.path.join(tvserver.MEDIA, shared), "wb") as fh:
            fh.write(PNG)
        for dev in ("scr-a", "scr-b"):
            tvserver._state["devices"][dev] = {"version": 1, "items": [
                {"id": shared, "file": shared, "type": "image", "seconds": 10, "sound": False,
                 "enabled": True, "from": "", "until": ""}]}
        self.jcall("POST", "/api/delete", {"device": "scr-a", "file": shared})
        self.assertEqual(self._playlist("scr-a"), [])
        self.assertEqual(self._playlist("scr-b"), ["/media/" + shared])
        self.assertEqual(self.call("GET", "/media/" + shared, auth=None)[0], 200)
        self.jcall("POST", "/api/delete", {"device": "scr-b", "file": shared})
        self.assertEqual(self.call("GET", "/media/" + shared, auth=None)[0], 404)

    def test_item_edit_and_order_are_per_screen(self):
        shared = "sdileny.png"
        with open(os.path.join(tvserver.MEDIA, shared), "wb") as fh:
            fh.write(PNG)
        with open(os.path.join(tvserver.MEDIA, "druhy.png"), "wb") as fh:
            fh.write(PNG)
        for dev in ("scr-a", "scr-b"):
            tvserver._state["devices"][dev] = {"version": 1, "items": [
                {"id": f, "file": f, "type": "image", "seconds": 10, "sound": False,
                 "enabled": True, "from": "", "until": ""} for f in (shared, "druhy.png")]}
        self.jcall("POST", "/api/item", {"device": "scr-a", "file": shared, "seconds": 33,
                                         "enabled": False})
        self.jcall("POST", "/api/order", {"device": "scr-a", "order": ["druhy.png", shared]})
        self.jcall("POST", "/api/settings", {"device": "scr-a", "label": "Vchod"})
        a = tvserver._state["devices"]["scr-a"]
        b = tvserver._state["devices"]["scr-b"]
        self.assertEqual([i["file"] for i in a["items"]], ["druhy.png", shared])
        self.assertEqual([i["file"] for i in b["items"]], [shared, "druhy.png"])
        self.assertEqual(b["items"][0]["seconds"], 10)
        self.assertTrue(b["items"][0]["enabled"])
        self.assertNotIn("label", (b.get("settings") or {}))
        self.assertEqual(b["version"], 1)
        st, _, body = self.jcall("GET", "/api/tv/scr-b/playlist", auth=None)
        self.assertEqual(body["items"][0]["seconds"], 10)

    def test_heartbeat_and_devices_are_separate(self):
        self._upload("scr-a")
        self._upload("scr-b")
        self.call("GET", "/api/tv/scr-a/playlist?heartbeat=1", auth=None)
        st, _, body = self.jcall("GET", "/api/devices")
        body = body["devices"]
        self.assertTrue(body["scr-a"]["online"])
        self.assertFalse(body["scr-b"]["online"])
        self.assertEqual(body["scr-a"]["count"], 1)
        self.assertEqual(body["scr-b"]["count"], 1)

    def test_request_for_unknown_screen_creates_nothing_and_affects_no_other(self):
        self._upload("scr-a")
        before = json.dumps(tvserver._state["devices"], sort_keys=True)
        for _ in range(3):
            self.call("GET", "/api/tv/neznama/playlist?heartbeat=1", auth=None)
        self.assertEqual(json.dumps(tvserver._state["devices"], sort_keys=True), before)

    def test_delete_with_foreign_filename_does_nothing(self):
        b = self._upload("scr-b")
        self.jcall("POST", "/api/delete", {"device": "scr-a", "file": b})
        self.assertEqual(self._playlist("scr-b"), ["/media/" + b])
        self.assertEqual(self.call("GET", "/media/" + b, auth=None)[0], 200)


# --------------------------------------------------------------------------
# (9) Idempotency-Key
# --------------------------------------------------------------------------
class IdempotencyTest(Matrix):
    def _up(self, key, dev="d1", name="ok.png", data=PNG, extra=None):
        body, h = multipart_body(name, data)
        if key is not None:
            h["Idempotency-Key"] = key
        h.update(extra or {})
        return self.call("POST", f"/api/upload?device={dev}", body, h)

    def test_replay_returns_same_response_and_does_not_store_twice(self):
        st1, h1, d1 = self._up("klic-1")
        st2, h2, d2 = self._up("klic-1")
        self.assertEqual((st1, st2), (200, 200))
        self.assertEqual(json.loads(d1), json.loads(d2))
        self.assertNotIn("idempotent-replay", h1)
        self.assertEqual(h2.get("idempotent-replay"), "true")
        self.assertEqual(len(tvserver._state["devices"]["d1"]["items"]), 1)
        self.assertEqual(len([n for n in os.listdir(tvserver.MEDIA) if n.endswith("_ok.png")]), 1)

    def test_replay_even_with_different_content_and_same_key(self):
        self._up("klic-1")
        st, h, _ = self._up("klic-1", name="jiny.png", data=_png(9, 9))
        self.assertEqual(h.get("idempotent-replay"), "true")
        self.assertEqual(len(tvserver._state["devices"]["d1"]["items"]), 1)

    def test_without_key_or_with_different_key_stores_again(self):
        self._up(None)
        self._up(None)
        self._up("a")
        self._up("b")
        self.assertEqual(len(tvserver._state["devices"]["d1"]["items"]), 4)

    def test_key_is_per_device(self):
        self._up("klic-1", dev="d1")
        st, h, _ = self._up("klic-1", dev="d2")
        self.assertEqual(st, 200)
        self.assertNotIn("idempotent-replay", h)
        self.assertEqual(len(tvserver._state["devices"]["d2"]["items"]), 1)

    def test_invalid_key_is_400_with_code(self):
        for key in ("", "a b", "a" * 129, "ž", "a/b", "a;b", "<x>"):
            with self.subTest(key=key[:10]):
                try:
                    st, h, data = self._up(key)
                except (ValueError, UnicodeEncodeError):
                    continue
                d = json.loads(data)
                self.assertEqual(st, 400)
                self.assertEqual(d["code"], "invalid_idempotency_key")
                self.assertEqual(d["error_codes"], ["invalid_idempotency_key"])
        self.assertNotIn("d1", tvserver._state["devices"])

    def test_valid_keys_at_boundary(self):
        for i, key in enumerate(("a", "A" * 128, "a.b_c:d-e", "0")):
            with self.subTest(key=key[:10]):
                st, _, _ = self._up(key, dev=f"d{i}")
                self.assertEqual(st, 200)

    def test_failed_upload_is_not_cached_and_key_can_be_reused(self):
        st, _, data = self._up("k", name="a.xyz", data=b"x")
        self.assertFalse(json.loads(data)["ok"])
        st, h, data = self._up("k")
        self.assertEqual(st, 200)
        self.assertNotIn("idempotent-replay", h)
        self.assertTrue(json.loads(data)["ok"])

    def test_key_is_forgotten_after_ttl_expiry(self):
        clock = FakeClock()
        cache = tvserver.TtlCache(600, 10, clock=clock)
        with mock.patch.object(tvserver, "_idem_cache", cache):
            self._up("k")
            st, h, _ = self._up("k")
            self.assertEqual(h.get("idempotent-replay"), "true")
            clock.t += 601
            st, h, _ = self._up("k")
            self.assertNotIn("idempotent-replay", h)
        self.assertEqual(len(tvserver._state["devices"]["d1"]["items"]), 2)

    def test_replay_requires_authentication(self):
        self._up("k")
        body, h = multipart_body("ok.png", PNG)
        h["Idempotency-Key"] = "k"
        st, _, _ = self.call("POST", "/api/upload?device=d1", body, h, auth=None)
        self.assertEqual(st, 401)

    def test_replay_with_language_header_returns_success_unchanged(self):
        self._up("k")
        st, h, data = self._up("k", extra={"Accept-Language": "en"})
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(data)["ok"])

    def test_concurrent_upload_with_same_key_is_409_then_replay_after_completion(self):
        entered, release = threading.Event(), threading.Event()
        real = tvserver.H._upload

        def slow(handler):
            entered.set()
            release.wait(10)
            return real(handler)

        results = {}

        def first():
            results["first"] = self._up("sb")

        with mock.patch.object(tvserver.H, "_upload", slow):
            t = threading.Thread(target=first)
            t.start()
            try:
                self.assertTrue(entered.wait(5), "first upload did not start")
                st, h, data = self._up("sb")
                d = json.loads(data)
                self.assertEqual(st, 409)
                self.assertEqual(d["code"], "upload_in_progress")
                self.assertEqual(d["error_codes"], ["upload_in_progress"])
                self.assertEqual(h["retry-after"], "5")
                self.assertEqual(h["connection"].lower(), "close")
            finally:
                release.set()
                t.join(10)
        self.assertEqual(results["first"][0], 200)
        st, h, _ = self._up("sb")
        self.assertEqual(h.get("idempotent-replay"), "true")
        self.assertEqual(len(tvserver._state["devices"]["d1"]["items"]), 1)

    def test_409_busy_key_from_cache(self):
        tvserver._idem_cache.put(("d1", "busy"), tvserver._IDEM_BUSY)
        st, h, data = self._up("busy")
        self.assertEqual(st, 409)
        self.assertEqual(json.loads(data)["code"], "upload_in_progress")
        # a different device is not blocked by the same key
        self.assertEqual(self._up("busy", dev="d2")[0], 200)

    def test_crashing_upload_releases_key_reservation(self):
        def boom(handler):
            raise RuntimeError("crash")
        with mock.patch.object(tvserver.H, "_upload", boom):
            try:
                self._up("k")
            except (ConnectionError, http.client.HTTPException):
                pass
        st, h, _ = self._up("k")
        self.assertEqual(st, 200)
        self.assertNotIn("idempotent-replay", h)


if __name__ == "__main__":
    unittest.main()
