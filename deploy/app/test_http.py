"""HTTP tests against a real server on a local port (isolated state)."""
import base64
import http.client
import json
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock
from http.server import ThreadingHTTPServer

import tvserver

AUTH = "Basic " + base64.b64encode(b"admin:tajne").decode()


class HttpBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="test-http-")
        cls.saved = {k: getattr(tvserver, k) for k in
                     ("STATE", "MEDIA", "TELEMETRY", "ADMIN_USER", "ADMIN_PASS",
                      "_state", "_online", "_state_recovery_required")}
        tvserver.STATE = os.path.join(cls.tmp, "state.json")
        tvserver.MEDIA = os.path.join(cls.tmp, "media")
        os.makedirs(tvserver.MEDIA)
        tvserver.TELEMETRY = os.path.join(cls.tmp, "telemetry.jsonl")
        tvserver.ADMIN_USER, tvserver.ADMIN_PASS = "admin", "tajne"
        tvserver._state_recovery_required = False
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), tvserver.H)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        for k, v in cls.saved.items():
            setattr(tvserver, k, v)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        tvserver._state = {"devices": {}, "pairs": {}}
        tvserver._online.clear()
        if os.path.exists(tvserver.TELEMETRY):
            os.remove(tvserver.TELEMETRY)

    def req(self, method, path, body=None, headers=None, auth=True):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = dict(headers or {})
        if auth:
            h["Authorization"] = AUTH
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        out = (r.status, dict(r.getheaders()), data)
        c.close()
        return out


class PublicPlaylistTest(HttpBase):
    def test_invalid_name_is_rejected(self):
        for name in ("a.b", "x%20y", "a" * 49):
            with self.subTest(name=name):
                st, _, _ = self.req("GET", f"/api/tv/{name}/playlist?heartbeat=1", auth=False)
                self.assertIn(st, (400, 404))
        self.assertEqual(tvserver._online, {})
        self.assertEqual(tvserver._state["devices"], {})

    def test_heartbeat_creates_neither_device_nor_telemetry(self):
        st, _, body = self.req("GET", "/api/tv/neznamy/playlist?heartbeat=1", auth=False)
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body)["items"], [])
        self.assertNotIn("neznamy", tvserver._state["devices"])
        self.assertNotIn("neznamy", tvserver._online)
        self.assertFalse(os.path.exists(tvserver.TELEMETRY))

    def test_heartbeat_works_for_existing_device(self):
        tvserver._state["devices"]["tv1"] = {"version": 1, "items": []}
        st, _, _ = self.req("GET", "/api/tv/tv1/playlist?heartbeat=1", auth=False)
        self.assertEqual(st, 200)
        self.assertIn("tv1", tvserver._online)

    def test_devices_reports_cached_items_count(self):
        tvserver._state["devices"]["tv1"] = {"version": 1, "items": []}
        self.req("GET", "/api/tv/tv1/playlist?heartbeat=1&cache=2/5", auth=False)
        st, _, body = self.req("GET", "/api/devices")
        self.assertEqual(st, 200)
        dev = json.loads(body)["devices"]["tv1"]
        self.assertEqual(dev["cached_items"], "2/5")
        self.assertNotIn("mem_mb", dev)

    def test_paired_device_is_created_via_claim(self):
        tvserver._state["pairs"]["ABCDEF"] = {"client": "c" * 20,
                                              "expires": 2 ** 31}
        st, _, _ = self.req("POST", "/api/pair/claim",
                            {"code": "ABCDEF", "device": "sal"})
        self.assertEqual(st, 200)
        self.assertIn("sal", tvserver._state["devices"])


class BodyLimitTest(HttpBase):
    def test_too_large_json_body_413_close(self):
        st, hdr, _ = self.req("POST", "/api/settings", None,
                              {"Content-Length": str(10 * 1024 * 1024),
                               "Content-Type": "application/json"})
        self.assertEqual(st, 413)
        self.assertEqual(hdr.get("Connection", "").lower(), "close")

    def test_invalid_content_length_400(self):
        for val in ("abc", "-5"):
            with self.subTest(val=val):
                st, _, _ = self.req("POST", "/api/settings", None,
                                    {"Content-Length": val})
                self.assertEqual(st, 400)

    def test_normal_json_body_passes(self):
        st, _, _ = self.req("POST", "/api/settings", {"device": "d1", "qrCorner": True})
        self.assertEqual(st, 200)


class KeepAliveLatencyTest(HttpBase):
    def test_further_keepalive_requests_are_not_slowed_by_nagle(self):
        """Headers and body are not sent as two writes with Nagle + delayed ACK (~41 ms)."""
        import time
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            times = []
            for _ in range(8):
                t0 = time.perf_counter()
                c.request("GET", "/api/health")
                r = c.getresponse()
                r.read()
                times.append((time.perf_counter() - t0) * 1000)
                self.assertEqual(r.status, 200)
        finally:
            c.close()
        rest = sorted(times[1:])
        median = rest[len(rest) // 2]
        self.assertLess(median, 25, f"keep-alive: median of further requests {median:.1f} ms ({times})")


class TimeoutTest(unittest.TestCase):
    def test_handler_has_socket_timeout(self):
        self.assertTrue(30 <= tvserver.H.timeout <= 120)


class AuthTest(HttpBase):
    def test_401_ma_connection_close(self):
        st, hdr, _ = self.req("POST", "/api/settings", {}, auth=False)
        self.assertEqual(st, 401)
        self.assertEqual(hdr.get("Connection", "").lower(), "close")

    def test_wrong_password_and_username(self):
        for cred in (b"admin:spatne", b"jiny:tajne", b"admin"):
            with self.subTest(cred=cred):
                h = {"Authorization": "Basic " + base64.b64encode(cred).decode()}
                st, _, _ = self.req("POST", "/api/settings", {}, h, auth=False)
                self.assertEqual(st, 401)

    def test_nonascii_password_does_not_crash_server(self):
        h = {"Authorization": "Basic " + base64.b64encode("admin:hesló".encode()).decode()}
        st, _, _ = self.req("POST", "/api/settings", {}, h, auth=False)
        self.assertEqual(st, 401)


class RootAuthTest(HttpBase):
    """GET / serves admin.html, so it must be behind Basic auth like /admin."""

    def test_root_without_password_is_401_and_no_admin_skeleton(self):
        for method in ("GET", "HEAD"):
            with self.subTest(method=method):
                st, hdr, body = self.req(method, "/", auth=False)
                self.assertEqual(st, 401)
                self.assertIn("Basic", hdr.get("WWW-Authenticate", ""))
                self.assertNotIn(b"<html", body.lower())
                self.assertNotIn(b"admin", body.lower())

    def test_root_with_query_without_password_is_401(self):
        st, _, _ = self.req("GET", "/?x=1", auth=False)
        self.assertEqual(st, 401)

    def test_root_with_password_returns_admin_like_admin(self):
        st, _, body = self.req("GET", "/")
        st2, _, body2 = self.req("GET", "/admin")
        self.assertEqual(st, 200)
        self.assertEqual(body, body2)
        self.assertIn(b"<html", body.lower())

    def test_public_paths_stay_without_password(self):
        for path in ("/tv/kuchyn", "/tv", "/connect", "/pair-qr.png", "/api/health",
                     "/api/tv/kuchyn/playlist", "/sw.js", "/robots.txt",
                     "/media/neexistuje.jpg", "/assets/neexistuje.js"):
            with self.subTest(path=path):
                st, _, _ = self.req("GET", path, auth=False)
                self.assertNotEqual(st, 401)


class AdminValidationTest(HttpBase):
    def test_body_not_object_400(self):
        for ep in ("settings", "order", "delete", "item"):
            for body in ("[1,2]", "5", '"x"'):
                with self.subTest(ep=ep, body=body):
                    st, _, _ = self.req("POST", f"/api/{ep}", body)
                    self.assertEqual(st, 400)

    def test_invalid_device_400(self):
        for ep in ("settings", "order", "delete", "item"):
            for dev in ("a b", "../x", 5, None, ["x"]):
                with self.subTest(ep=ep, dev=dev):
                    st, _, _ = self.req("POST", f"/api/{ep}", {"device": dev, "file": "a.png"})
                    self.assertEqual(st, 400)
        self.assertEqual(tvserver._state["devices"], {})

    def test_order_must_be_list_of_strings(self):
        for order in ("abc", 5, {"a": 1}, [1, 2], [["x"]]):
            with self.subTest(order=order):
                st, _, _ = self.req("POST", "/api/order", {"device": "d1", "order": order})
                self.assertEqual(st, 400)

    def test_seconds_bounds_and_type(self):
        p = os.path.join(tvserver.MEDIA, "a.png")
        with open(p, "wb") as fh:
            fh.write(b"x")
        tvserver._state["devices"]["d1"] = {"version": 1, "items": [
            {"file": "a.png", "type": "image", "seconds": 10}]}
        for sec in ("abc", None, [5], 10 ** 9, float("inf"), True):
            with self.subTest(sec=sec):
                st, _, _ = self.req("POST", "/api/item",
                                    '{"device":"d1","file":"a.png","seconds":%s}'
                                    % json.dumps(sec).replace("Infinity", "1e999"))
                self.assertEqual(st, 400)
        st, _, _ = self.req("POST", "/api/item", {"device": "d1", "file": "a.png", "seconds": 7})
        self.assertEqual(st, 200)
        self.assertEqual(tvserver._state["devices"]["d1"]["items"][0]["seconds"], 7)

    def test_file_must_be_string(self):
        for ep in ("delete", "item"):
            st, _, _ = self.req("POST", f"/api/{ep}", {"device": "d1", "file": ["x"]})
            self.assertEqual(st, 400)


class PairStatusSaveTest(HttpBase):
    CLIENT = "c" * 20

    def _calls(self, path):
        with mock.patch.object(tvserver, "save") as sv:
            st, _, body = self.req("GET", path, auth=False)
        return st, body, sv.call_count

    def test_nonexistent_code_does_not_save(self):
        st, _, n = self._calls("/api/pair/status?client=" + self.CLIENT)
        self.assertEqual((st, n), (404, 0))

    def test_pending_code_does_not_save(self):
        tvserver._state["pairs"]["ABCDEF"] = {"client": self.CLIENT, "expires": 2 ** 31}
        st, _, n = self._calls("/api/pair/status?client=" + self.CLIENT)
        self.assertEqual((st, n), (200, 0))

    def test_expired_code_does_not_save(self):
        tvserver._state["pairs"]["ABCDEF"] = {"client": self.CLIENT, "expires": 1}
        st, _, n = self._calls("/api/pair/status?client=" + self.CLIENT)
        self.assertEqual((st, n), (404, 0))

    def test_paired_code_saves_once(self):
        tvserver._state["pairs"]["ABCDEF"] = {"client": self.CLIENT, "expires": 2 ** 31,
                                              "device": "sal"}
        st, body, n = self._calls("/api/pair/status?client=" + self.CLIENT)
        self.assertEqual((st, n), (200, 1))
        self.assertEqual(json.loads(body)["device"], "sal")

    def test_save_failure_does_not_break_public_get(self):
        tvserver._state["pairs"]["ABCDEF"] = {"client": self.CLIENT, "expires": 2 ** 31,
                                              "device": "sal"}
        with mock.patch.object(tvserver, "save", side_effect=OSError("disk")):
            st, _, body = self.req("GET", "/api/pair/status?client=" + self.CLIENT, auth=False)
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body)["status"], "paired")


class AdminRollbackTest(HttpBase):
    """A8: a failing save() restores memory to the original state and the client gets a JSON error."""

    def _seed(self):
        for n in ("a.png", "b.png"):
            with open(os.path.join(tvserver.MEDIA, n), "wb") as fh:
                fh.write(b"x")
        tvserver._state["devices"]["d1"] = {"version": 5, "items": [
            {"file": "a.png", "type": "image", "seconds": 10, "sound": False,
             "enabled": True, "from": "", "until": "", "id": "a.png"},
            {"file": "b.png", "type": "image", "seconds": 10, "sound": False,
             "enabled": True, "from": "", "until": "", "id": "b.png"}]}
        return json.loads(json.dumps(tvserver._state["devices"]["d1"]))

    def _fail(self, path, body, exc=None):
        with mock.patch.object(tvserver, "save", side_effect=exc or OSError("disk")):
            return self.req("POST", path, body)

    def test_set_settings_rollback(self):
        before = self._seed()
        st, _, body = self._fail("/api/settings", {"device": "d1", "qrCorner": True})
        self.assertEqual(st, 500)
        self.assertIn("error", json.loads(body))
        self.assertEqual(tvserver._state["devices"]["d1"], before)

    def test_reorder_rollback(self):
        before = self._seed()
        st, _, body = self._fail("/api/order", {"device": "d1", "order": ["b.png", "a.png"]})
        self.assertEqual(st, 500)
        self.assertIn("error", json.loads(body))
        self.assertEqual(tvserver._state["devices"]["d1"], before)

    def test_delete_rollback_and_file_remains(self):
        before = self._seed()
        st, _, _ = self._fail("/api/delete", {"device": "d1", "file": "a.png"})
        self.assertEqual(st, 500)
        self.assertEqual(tvserver._state["devices"]["d1"], before)
        self.assertTrue(os.path.exists(os.path.join(tvserver.MEDIA, "a.png")))

    def test_edit_item_rollback(self):
        before = self._seed()
        st, _, _ = self._fail("/api/item", {"device": "d1", "file": "a.png", "seconds": 33})
        self.assertEqual(st, 500)
        self.assertEqual(tvserver._state["devices"]["d1"], before)

    def test_full_disk_is_507(self):
        before = self._seed()
        exc = OSError(28, "No space left on device")
        st, _, _ = self._fail("/api/settings", {"device": "d1", "qrCorner": True}, exc)
        self.assertEqual(st, 507)
        self.assertEqual(tvserver._state["devices"]["d1"], before)

    def test_new_device_is_not_created_on_failure(self):
        st, _, _ = self._fail("/api/settings", {"device": "nove", "qrCorner": True})
        self.assertEqual(st, 500)
        self.assertNotIn("nove", tvserver._state["devices"])

    def test_next_request_works_after_failure(self):
        self._seed()
        self._fail("/api/item", {"device": "d1", "file": "a.png", "seconds": 33})
        st, _, _ = self.req("POST", "/api/item", {"device": "d1", "file": "a.png", "seconds": 7})
        self.assertEqual(st, 200)
        self.assertEqual(tvserver._state["devices"]["d1"]["version"], 6)


class HealthRecoveryTest(HttpBase):
    def test_healthy_state_reports_ok_and_state_ok(self):
        st, _, body = self.req("GET", "/api/health", auth=False)
        d = json.loads(body)
        self.assertEqual(st, 200)
        self.assertIs(d["ok"], True)
        self.assertIs(d["state_ok"], True)

    def test_recovery_required_neni_ok(self):
        tvserver._state_recovery_required = True
        try:
            st, _, body = self.req("GET", "/api/health", auth=False)
        finally:
            tvserver._state_recovery_required = False
        d = json.loads(body)
        # HTTP 200 stays: deploy.sh/smoke-test.sh read the body (ok:true), curl --fail is not surprised
        self.assertEqual(st, 200)
        self.assertIs(d["ok"], False)
        self.assertIs(d["state_ok"], False)
        self.assertTrue(d["state_error"])
        for k in ("version", "devices", "items", "max_file_mb"):
            self.assertIn(k, d)


class PairQrTest(HttpBase):
    def setUp(self):
        super().setUp()
        tvserver._qr_cache.clear()
        tvserver._state["pairs"]["ABCDEF"] = {"client": "c" * 20, "expires": 2 ** 31}
        self._env = mock.patch.dict(os.environ, {"SIGNAGE_PUBLIC_URL": ""})
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def qr(self, host="tv.example.cz", proto=None):
        h = {"Host": host}
        if proto is not None:
            h["X-Forwarded-Proto"] = proto
        return self.req("GET", "/pair-qr.png?code=ABCDEF", headers=h, auth=False)

    def test_cache_is_capped_and_lru(self):
        for i in range(tvserver.QR_CACHE_MAX + 50):
            st, _, _ = self.qr(host=f"h{i}.example.cz")
            self.assertEqual(st, 200)
        self.assertLessEqual(len(tvserver._qr_cache), tvserver.QR_CACHE_MAX)
        # the most recently requested one stays, the oldest is evicted
        keys = list(tvserver._qr_cache)
        self.assertTrue(any("h%d.example.cz" % (tvserver.QR_CACHE_MAX + 49) in k for k in keys))
        self.assertFalse(any("//h0.example.cz/" in k for k in keys))

    def test_invalid_forwarded_proto_400(self):
        for proto in ("javascript", "ftp", "http://evil", "h t"):
            with self.subTest(proto=proto):
                st, _, _ = self.qr(proto=proto)
                self.assertEqual(st, 400)
        self.assertEqual(len(tvserver._qr_cache), 0)

    def test_valid_forwarded_proto(self):
        for proto in ("https", "HTTP", "https, http"):
            with self.subTest(proto=proto):
                st, hdr, _ = self.qr(proto=proto)
                self.assertEqual(st, 200)
                self.assertEqual(hdr["Content-Type"], "image/png")
        self.assertTrue(any(k.startswith("https://") for k in tvserver._qr_cache))

    def test_too_long_host_400(self):
        st, _, _ = self.qr(host="a" * 300 + ".cz")
        self.assertEqual(st, 400)

    def test_invalid_host_400(self):
        for host in ("a b", "x/y", "a@b", "evil.cz:99999999"):
            with self.subTest(host=host):
                st, _, _ = self.qr(host=host)
                self.assertEqual(st, 400)


def multipart_body(filename, data, boundary="XBOUNDX"):
    body = (b"--" + boundary.encode() + b"\r\n"
            + ('Content-Disposition: form-data; name="f0"; filename="%s"\r\n'
               "Content-Type: application/octet-stream\r\n\r\n" % filename).encode()
            + data + b"\r\n--" + boundary.encode() + b"--\r\n")
    return body, {"Content-Type": "multipart/form-data; boundary=" + boundary}


class UploadLimitsTest(HttpBase):
    def test_file_over_limit_413_and_close(self):
        body, h = multipart_body("a.png", b"x" * 20000)
        with mock.patch.object(tvserver, "MAX_FILE_BYTES", 5000):
            st, hdr, resp = self.req("POST", "/api/upload?device=d1", body, h)
        self.assertEqual(st, 413)
        self.assertEqual(hdr.get("Connection", "").lower(), "close")
        d = json.loads(resp)
        self.assertFalse(d["ok"])
        self.assertTrue(d["errors"])
        self.assertEqual(os.listdir(tvserver.MEDIA), [])

    def test_too_large_file_is_not_read_fully(self):
        # the server reads only a fraction of the body: count the bytes it pulled from rfile
        body, h = multipart_body("a.png", b"x" * (3 * 1024 * 1024))
        read = []
        orig = tvserver.parse_multipart

        def spy(fp, ctype, clen, maxb, **kw):
            class Counting:
                def read(self_inner, n=-1):
                    chunk = fp.read(n)
                    read.append(len(chunk))
                    return chunk
            return orig(Counting(), ctype, clen, maxb, **kw)

        with mock.patch.object(tvserver, "MAX_FILE_BYTES", 100 * 1024), \
                mock.patch.object(tvserver, "parse_multipart", spy):
            st, _, _ = self.req("POST", "/api/upload?device=d1", body, h)
        self.assertEqual(st, 413)
        self.assertLess(sum(read), len(body) // 4)

    def test_file_within_limit_passes_and_slot_is_released(self):
        png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        body, h = multipart_body("ok.png", png)
        st, _, _ = self.req("POST", "/api/upload?device=d1", body, h)
        self.assertIn(st, (200, 400, 413))          # we do not test content validation here
        # the slot was returned: the semaphore can be acquired in full count again
        got = [tvserver._upload_slots.acquire(timeout=5)
               for _ in range(tvserver.MAX_CONCURRENT_UPLOADS)]
        try:
            self.assertTrue(all(got))
        finally:
            for g in got:
                if g:
                    tvserver._upload_slots.release()

    def _take_all_slots(self):
        """Deterministically takes ALL slots (waits for handlers that are still finishing)."""
        got = []
        for _ in range(tvserver.MAX_CONCURRENT_UPLOADS):
            self.assertTrue(tvserver._upload_slots.acquire(timeout=5),
                            "slot was not released after the previous test")
            got.append(1)
        return got

    def test_concurrent_uploads_over_limit_503(self):
        # we hold all slots for the whole request; no handler from previous tests
        # holds any (acquire with timeout), so the result does not depend on timing
        held = self._take_all_slots()
        try:
            body, h = multipart_body("a.png", b"x" * 10)
            st, hdr, resp = self.req("POST", "/api/upload?device=d1", body, h)
        finally:
            for _ in held:
                tvserver._upload_slots.release()
        self.assertEqual(st, 503)
        self.assertTrue(hdr.get("Retry-After", "").isdigit())
        self.assertEqual(hdr.get("Connection", "").lower(), "close")
        self.assertFalse(json.loads(resp)["ok"])

    def test_503_and_429_with_large_body_reach_client_instead_of_reset(self):
        """A rejected upload with a large body: the client gets a response, not ConnectionReset."""
        body, h = multipart_body("velky.png", b"x" * (8 * 1024 * 1024))
        held = self._take_all_slots()
        try:
            st, hdr, resp = self.req("POST", "/api/upload?device=d1", body, h)
        finally:
            for _ in held:
                tvserver._upload_slots.release()
        self.assertEqual(st, 503)
        self.assertTrue(hdr.get("Retry-After", "").isdigit())
        self.assertEqual(json.loads(resp)["code"], "server_busy")
        with mock.patch.object(tvserver._upload_limiter, "acquire", return_value=7):
            st, hdr, resp = self.req("POST", "/api/upload?device=d1", body, h)
        self.assertEqual(st, 429)
        self.assertEqual(hdr.get("Retry-After"), "7")
        self.assertEqual(json.loads(resp)["code"], "too_many_uploads")

    def test_unauthenticated_upload_does_not_take_slot(self):
        body, h = multipart_body("a.png", b"x" * 10)
        st, _, _ = self.req("POST", "/api/upload?device=d1", body, h, auth=False)
        self.assertEqual(st, 401)
        got = [tvserver._upload_slots.acquire(timeout=5)
               for _ in range(tvserver.MAX_CONCURRENT_UPLOADS)]
        try:
            self.assertTrue(all(got))
        finally:
            for g in got:
                if g:
                    tvserver._upload_slots.release()


if __name__ == "__main__":
    unittest.main()
