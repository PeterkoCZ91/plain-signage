"""Per-device brand: default = neutral demo content, saving, validation, legacy data."""
import json

import tvserver
from test_http import HttpBase

CURRENT_BRAND = {"name": "Your Brand",
        "email": "info@example.com",
        "phone": "+000 000 000 000",
        "city": "Your City",
        "url": "https://example.com/",
        "qrEmail": "info@example.com",
        "pwaTitle": "Signage",
        "wordmark": "", "taglineCs": "", "taglineEn": "",
        "extraQrUrl": "", "extraQrLabelCs": "", "extraQrLabelEn": ""}


class BrandFunctionsTest(HttpBase):
    def test_default_is_exactly_neutral_demo_content(self):
        self.assertEqual(tvserver.brand_of({}), CURRENT_BRAND)
        self.assertEqual(tvserver.DEFAULT_QR_URL, CURRENT_BRAND["url"])
        self.assertEqual(tvserver.DEFAULT_QR_EMAIL, CURRENT_BRAND["qrEmail"])

    def test_missing_or_broken_brand_is_default(self):
        for s in ({}, {"brand": None}, {"brand": "x"}, {"brand": []},
                  {"brand": {"email": 5, "phone": None, "zzz": "a"}}):
            with self.subTest(s=s):
                self.assertEqual(tvserver.brand_of(s), CURRENT_BRAND)

    def test_empty_field_means_default(self):
        b = tvserver.brand_of({"brand": {"city": "  ", "name": "Studio X"}})
        self.assertEqual(b["city"], "Your City")
        self.assertEqual(b["name"], "Studio X")

    def test_qr_cards_without_brand_have_no_placeholder_qr(self):
        self.assertEqual(tvserver.qr_cards({}), [])
        self.assertEqual(tvserver.playlist_settings({})["qrCards"], [])

    def test_qr_cards_real_url_with_placeholder_email(self):
        cards = tvserver.qr_cards({"brand": {"url": "https://x.cz/"}})
        self.assertEqual([c["url"] for c in cards], ["https://x.cz/"])

    def test_qr_cards_take_target_from_brand(self):
        s = {"brand": {"url": "https://example.com/x", "qrEmail": "a@example.com"}}
        cards = tvserver.qr_cards(s)
        self.assertEqual([c["url"] for c in cards], ["https://example.com/x", "a@example.com"])


class BrandHttpTest(HttpBase):
    def playlist(self, dev="d1"):
        st, _, body = self.req("GET", f"/api/tv/{dev}/playlist", auth=False)
        self.assertEqual(st, 200)
        return json.loads(body)

    def test_playlist_without_brand_returns_current_values(self):
        tvserver._state["devices"]["d1"] = {"version": 1, "items": []}
        d = self.playlist()
        self.assertEqual(d["settings"]["brand"], CURRENT_BRAND)
        self.assertEqual(d["settings"]["qrUrl"], CURRENT_BRAND["url"])

    def test_legacy_device_without_settings_works(self):
        tvserver._state["devices"]["d1"] = {"version": 1, "items": []}
        self.assertEqual(self.playlist()["settings"]["brand"], CURRENT_BRAND)

    def test_save_and_return(self):
        b = {"name": "Studio X", "email": "info@x.cz", "phone": "+420 111 222 333",
             "city": "Brno", "url": "https://x.cz/", "qrEmail": "qr@example.com",
             "pwaTitle": "StudioX"}
        st, _, body = self.req("POST", "/api/settings", {"device": "d1", "brand": b})
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body)["settings"]["brand"], b)
        self.assertEqual(self.playlist()["settings"]["brand"], {**CURRENT_BRAND, **b})
        # another device stays on defaults
        self.assertEqual(self.playlist("d2")["settings"]["brand"], CURRENT_BRAND)

    def test_brand_changes_default_qr_target(self):
        self.req("POST", "/api/settings",
                 {"device": "d1", "brand": {"url": "https://x.cz/", "qrEmail": "qr@example.com"}})
        s = self.playlist()["settings"]
        self.assertEqual(s["qrUrl"], "https://x.cz/")
        self.assertEqual([c["url"] for c in s["qrCards"]], ["https://x.cz/", "qr@example.com"])

    def test_changing_other_setting_keeps_brand(self):
        self.req("POST", "/api/settings", {"device": "d1", "brand": {"city": "Brno"}})
        self.req("POST", "/api/settings", {"device": "d1", "qrCorner": True})
        self.assertEqual(self.playlist()["settings"]["brand"]["city"], "Brno")

    def test_empty_field_restores_default(self):
        self.req("POST", "/api/settings", {"device": "d1", "brand": {"city": "Brno"}})
        self.req("POST", "/api/settings", {"device": "d1", "brand": {"city": ""}})
        self.assertEqual(self.playlist()["settings"]["brand"]["city"], "Your City")

    def test_validation_rejects_html(self):
        for field in ("name", "email", "phone", "city", "pwaTitle"):
            with self.subTest(field=field):
                st, _, _ = self.req("POST", "/api/settings", {
                    "device": "d1", "brand": {field: "<script>alert(1)</script>"}})
                self.assertEqual(st, 400)
        self.assertNotIn("brand", tvserver._state["devices"].get("d1", {}).get("settings", {}))

    def test_validation_rejects_dangerous_url(self):
        for bad in ("javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,x",
                    "ftp://x.cz", "vbscript:x", "//x.cz", "neco bez schematu",
                    "https://x.cz/<b>"):
            with self.subTest(bad=bad):
                st, _, _ = self.req("POST", "/api/settings",
                                    {"device": "d1", "brand": {"url": bad}})
                self.assertEqual(st, 400)

    def test_url_allows_http_https_mailto_and_bare_email(self):
        for ok in ("http://x.cz", "https://x.cz/a?b=1", "mailto:a@example.com", "a@example.com"):
            with self.subTest(ok=ok):
                st, _, _ = self.req("POST", "/api/settings",
                                    {"device": "d1", "brand": {"url": ok}})
                self.assertEqual(st, 200)

    def test_validation_of_types_and_lengths(self):
        bad = [{"name": 5}, {"name": ["a"]}, {"email": {"a": 1}}, {"name": "x" * 500},
               {"email": "neni-email"}, {"qrEmail": "javascript:x"},
               {"phone": "a\nb"}]
        for b in bad:
            with self.subTest(b=b):
                st, _, _ = self.req("POST", "/api/settings", {"device": "d1", "brand": b})
                self.assertEqual(st, 400)
        for notdict in ("x", 5, [], True):
            with self.subTest(notdict=notdict):
                st, _, _ = self.req("POST", "/api/settings",
                                    {"device": "d1", "brand": notdict})
                self.assertEqual(st, 400)

    def test_brand_null_clears_override(self):
        self.req("POST", "/api/settings", {"device": "d1", "brand": {"city": "Brno"}})
        st, _, _ = self.req("POST", "/api/settings", {"device": "d1", "brand": None})
        self.assertEqual(st, 200)
        self.assertEqual(self.playlist()["settings"]["brand"], CURRENT_BRAND)

    def test_without_auth_401(self):
        st, _, _ = self.req("POST", "/api/settings",
                            {"device": "d1", "brand": {"city": "X"}}, auth=False)
        self.assertEqual(st, 401)

    def test_qr_png_uses_brand(self):
        from unittest import mock
        self.req("POST", "/api/settings",
                 {"device": "d1", "brand": {"url": "https://x.cz/", "qrEmail": "q@example.com"}})
        seen = []
        real = tvserver._qr_cache_get
        with mock.patch.object(tvserver, "_qr_cache_get",
                               side_effect=lambda u: (seen.append(u), real(u))[1]):
            self.req("GET", "/qr.png?device=d1", auth=False)
            self.req("GET", "/qr.png?device=d1&slot=2", auth=False)
        if seen:  # without segno the cache is not used
            self.assertEqual(seen, ["https://x.cz/", "mailto:q@example.com"])


def test_bind_default_and_env():
    import subprocess
    import sys
    code = "import tvserver; print(tvserver.BIND)"
    for env_val, want in ((None, "0.0.0.0"), ("127.0.0.1", "127.0.0.1")):
        import os
        env = {k: v for k, v in os.environ.items() if k != "SIGNAGE_BIND"}
        if env_val:
            env["SIGNAGE_BIND"] = env_val
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=os.path.dirname(__file__) or ".")
        assert out.stdout.strip().splitlines()[-1] == want, out.stderr
