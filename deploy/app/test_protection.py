"""Rate limit, upload idempotency key and cleanup after startup."""
import base64
import json
import os
import time
import unittest
from unittest import mock

import tvserver
from test_http import HttpBase, multipart_body

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class RateLimiterTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()

    def limiter(self, limit=3, window=60, max_keys=100):
        return tvserver.RateLimiter(limit, window, clock=self.clock, max_keys=max_keys)

    def test_under_limit_passes_over_limit_returns_retry_after(self):
        rl = self.limiter()
        for _ in range(3):
            self.assertEqual(rl.retry_after("ip"), 0)
            rl.hit("ip")
        self.assertGreater(rl.retry_after("ip"), 0)

    def test_retry_after_is_until_oldest_entry_expires(self):
        rl = self.limiter()
        rl.hit("ip")
        self.clock.t += 20
        rl.hit("ip")
        rl.hit("ip")
        self.assertEqual(rl.retry_after("ip"), 40)

    def test_window_expires(self):
        rl = self.limiter()
        for _ in range(3):
            rl.hit("ip")
        self.clock.t += 61
        self.assertEqual(rl.retry_after("ip"), 0)

    def test_keys_are_independent(self):
        rl = self.limiter()
        for _ in range(3):
            rl.hit("a")
        self.assertEqual(rl.retry_after("b"), 0)

    def test_retry_after_does_not_consume(self):
        rl = self.limiter()
        for _ in range(50):
            rl.retry_after("ip")
        self.assertEqual(rl.retry_after("ip"), 0)

    def test_acquire_consumes_and_rejects(self):
        rl = self.limiter(limit=2)
        self.assertEqual(rl.acquire("ip"), 0)
        self.assertEqual(rl.acquire("ip"), 0)
        self.assertGreater(rl.acquire("ip"), 0)

    def test_lru_dict_cap(self):
        rl = self.limiter(max_keys=5)
        for i in range(50):
            rl.hit("ip%d" % i)
        self.assertLessEqual(len(rl), 5)

    def test_lru_evicts_oldest_not_newest(self):
        rl = self.limiter(limit=1, max_keys=2)
        rl.hit("a")
        rl.hit("b")
        rl.hit("c")
        self.assertEqual(rl.retry_after("a"), 0)      # evicted
        self.assertGreater(rl.retry_after("c"), 0)


class TtlCacheTest(unittest.TestCase):
    def test_ttl_a_lru(self):
        clock = FakeClock()
        c = tvserver.TtlCache(ttl=10, max_items=2, clock=clock)
        c.put("a", 1)
        self.assertEqual(c.get("a"), 1)
        clock.t += 11
        self.assertIsNone(c.get("a"))
        c.put("a", 1)
        c.put("b", 2)
        c.put("c", 3)
        self.assertIsNone(c.get("a"))
        self.assertEqual(c.get("c"), 3)
        self.assertEqual(len(c), 2)


class RateLimitHttpBase(HttpBase):
    def setUp(self):
        super().setUp()
        tvserver.reset_rate_limits()
        self.addCleanup(tvserver.reset_rate_limits)

    def bad_login(self, headers=None):
        bad = "Basic " + base64.b64encode(b"admin:spatne").decode()
        h = {"Authorization": bad}
        h.update(headers or {})
        return self.req("GET", "/api/devices", headers=h, auth=False)


class LoginLimitTest(RateLimitHttpBase):
    def test_after_failed_login_limit_429_with_retry_after(self):
        for _ in range(tvserver.LOGIN_FAIL_MAX):
            self.assertEqual(self.bad_login()[0], 401)
        st, hdr, _ = self.bad_login()
        self.assertEqual(st, 429)
        self.assertTrue(hdr.get("Retry-After", "").isdigit())
        self.assertGreater(int(hdr["Retry-After"]), 0)

    def test_successful_login_does_not_consume_limit(self):
        for _ in range(tvserver.LOGIN_FAIL_MAX * 3):
            st, _, _ = self.req("GET", "/api/devices")
            self.assertNotEqual(st, 429)
            self.assertNotEqual(st, 401)

    def test_request_without_header_is_not_counted(self):
        for _ in range(tvserver.LOGIN_FAIL_MAX * 2):
            self.assertEqual(self.req("GET", "/api/devices", auth=False)[0], 401)
        self.assertNotEqual(self.req("GET", "/api/devices")[0], 429)

    def test_blocked_ip_has_no_access_even_with_correct_password(self):
        for _ in range(tvserver.LOGIN_FAIL_MAX):
            self.bad_login()
        self.assertEqual(self.req("GET", "/api/devices")[0], 429)

    def test_public_endpoints_stay_available_after_block(self):
        for _ in range(tvserver.LOGIN_FAIL_MAX + 1):
            self.bad_login()
        st, _, _ = self.req("GET", "/api/tv/neznamy/playlist", auth=False)
        self.assertEqual(st, 200)

    def test_xff_ignored_without_trust_proxy(self):
        with mock.patch.object(tvserver, "TRUST_PROXY", False):
            for i in range(tvserver.LOGIN_FAIL_MAX):
                self.bad_login({"X-Forwarded-For": "10.0.0.%d" % i})
            st, _, _ = self.bad_login({"X-Forwarded-For": "10.9.9.9"})
        self.assertEqual(st, 429)

    def test_xff_used_with_trust_proxy(self):
        with mock.patch.object(tvserver, "TRUST_PROXY", True):
            for _ in range(tvserver.LOGIN_FAIL_MAX):
                self.bad_login({"X-Forwarded-For": "10.0.0.1"})
            self.assertEqual(self.bad_login({"X-Forwarded-For": "10.0.0.1"})[0], 429)
            self.assertEqual(self.bad_login({"X-Forwarded-For": "10.0.0.2"})[0], 401)

    def test_trust_proxy_takes_last_xff_entry(self):
        with mock.patch.object(tvserver, "TRUST_PROXY", True):
            for i in range(tvserver.LOGIN_FAIL_MAX):
                self.bad_login({"X-Forwarded-For": "203.0.113.%d, 10.0.0.1" % i})
            self.assertEqual(
                self.bad_login({"X-Forwarded-For": "198.51.100.7, 10.0.0.1"})[0], 429)


class PairLimitTest(RateLimitHttpBase):
    def test_pair_request_per_ip_429(self):
        for i in range(tvserver.PAIR_REQUEST_MAX):
            st, _, _ = self.req("POST", "/api/pair/request",
                                {"client": "c%019d" % i}, auth=False)
            self.assertEqual(st, 200)
        st, hdr, _ = self.req("POST", "/api/pair/request",
                              {"client": "x" * 20}, auth=False)
        self.assertEqual(st, 429)
        self.assertTrue(hdr.get("Retry-After", "").isdigit())

    def test_invalid_identity_also_consumes_limit(self):
        for _ in range(tvserver.PAIR_REQUEST_MAX):
            self.req("POST", "/api/pair/request", {"client": "!"}, auth=False)
        st, _, _ = self.req("POST", "/api/pair/request",
                            {"client": "x" * 20}, auth=False)
        self.assertEqual(st, 429)


class UploadLimitTest(RateLimitHttpBase):
    def test_upload_per_ip_429(self):
        body, h = multipart_body("a.png", PNG)
        for _ in range(tvserver.UPLOAD_MAX):
            st, _, _ = self.req("POST", "/api/upload?device=d1", body, h)
            self.assertNotEqual(st, 429)
        st, hdr, resp = self.req("POST", "/api/upload?device=d1", body, h)
        self.assertEqual(st, 429)
        self.assertTrue(hdr.get("Retry-After", "").isdigit())
        self.assertEqual(hdr.get("Connection", "").lower(), "close")
        self.assertFalse(json.loads(resp)["ok"])


class IdempotencyTest(RateLimitHttpBase):
    def setUp(self):
        super().setUp()
        tvserver._idem_cache.clear()
        self.addCleanup(tvserver._idem_cache.clear)

    def upload(self, key, dev="d1", name="a.png"):
        body, h = multipart_body(name, PNG)
        if key:
            h["Idempotency-Key"] = key
        return self.req("POST", "/api/upload?device=" + dev, body, h)

    def test_repeat_returns_original_response_without_duplicate(self):
        self._pre = len([n for n in os.listdir(tvserver.MEDIA)
                         if n.endswith("_a.png") or n == "a.png"])
        st1, _, r1 = self.upload("klic-1")
        st2, hdr2, r2 = self.upload("klic-1")
        self.assertEqual(st1, 200)
        self.assertEqual((st2, r2), (st1, r1))
        self.assertEqual(hdr2.get("Idempotent-Replay"), "true")
        self.assertEqual(len(tvserver._state["devices"]["d1"]["items"]), 1)
        saved = json.loads(r1)["saved"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(len([n for n in os.listdir(tvserver.MEDIA)
                              if n.endswith("_a.png") or n == "a.png"]) - self._pre, 1)

    def test_without_key_behaves_as_before(self):
        self.upload(None)
        self.upload(None)
        self.assertEqual(len(tvserver._state["devices"]["d1"]["items"]), 2)

    def test_other_key_or_other_device_is_new_upload(self):
        self.upload("k1")
        self.upload("k2")
        self.upload("k1", dev="d2")
        self.assertEqual(len(tvserver._state["devices"]["d1"]["items"]), 2)
        self.assertEqual(len(tvserver._state["devices"]["d2"]["items"]), 1)

    def test_invalid_key_400(self):
        st, _, _ = self.upload("x" * 300)
        self.assertEqual(st, 400)

    def test_failed_upload_is_not_cached(self):
        with mock.patch.object(tvserver, "MAX_ITEMS", 1):
            self.upload("k9", name="a.png")
            _, _, r = self.upload("k9x", name="b.png")      # item limit -> error
            self.assertFalse(json.loads(r)["ok"])
            tvserver._state["devices"]["d1"]["items"].clear()
            _, hdr, r = self.upload("k9x", name="b.png")     # same key, not a replay
            self.assertTrue(json.loads(r)["ok"])
            self.assertIsNone(hdr.get("Idempotent-Replay"))

    def test_in_progress_upload_with_same_key_409(self):
        tvserver._idem_begin("d1", "busy")
        try:
            st, hdr, _ = self.upload("busy")
        finally:
            tvserver._idem_finish("d1", "busy", None)
        self.assertEqual(st, 409)
        self.assertTrue(hdr.get("Retry-After", "").isdigit())

    def test_replay_does_not_take_slot(self):
        self.upload("k1")
        slots = [tvserver._upload_slots.acquire(timeout=5)
                 for _ in range(tvserver.MAX_CONCURRENT_UPLOADS)]
        try:
            st, _, _ = self.upload("k1")
        finally:
            for s in slots:
                if s:
                    tvserver._upload_slots.release()
        self.assertEqual(st, 200)


class UploadCleanupTest(unittest.TestCase):
    def setUp(self):
        import shutil
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="test-cleanup-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = mock.patch.object(tvserver, "MEDIA", self.tmp)
        p.start()
        self.addCleanup(p.stop)

    def make(self, name, age_s):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as f:
            f.write(b"x")
        t = time.time() - age_s
        os.utime(path, (t, t))
        return path

    def test_deletes_stale_temp_files_of_both_types(self):
        a = self.make(".signage-upload-abc", 7200)
        b = self.make(".oprava-xyz.mp4", 7200)
        self.assertEqual(tvserver.cleanup_stale_uploads(), 2)
        self.assertFalse(os.path.exists(a) or os.path.exists(b))

    def test_keeps_fresh_files_and_normal_media(self):
        fresh = self.make(".signage-upload-new", 10)
        media = self.make("foto.png", 99999)
        other = self.make(".jiny-soubor", 99999)
        self.assertEqual(tvserver.cleanup_stale_uploads(), 0)
        for p in (fresh, media, other):
            self.assertTrue(os.path.exists(p))

    def test_logs_count(self):
        self.make(".signage-upload-a", 7200)
        with mock.patch("sys.stderr") as err:
            tvserver.cleanup_stale_uploads()
        out = "".join(c.args[0] for c in err.write.call_args_list)
        self.assertIn("1", out)

    def test_missing_directory_is_fine(self):
        with mock.patch.object(tvserver, "MEDIA", os.path.join(self.tmp, "neni")):
            self.assertEqual(tvserver.cleanup_stale_uploads(), 0)

    def test_repair_file_not_counted_in_quota(self):
        self.make(".oprava-x.mp4", 0)
        self.make("a.png", 0)
        self.assertEqual(tvserver.storage_bytes(), 1)


if __name__ == "__main__":
    unittest.main()
