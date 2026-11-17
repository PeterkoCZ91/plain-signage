"""Bilingual API errors: stable `code`, `error` in cs/en, Content-Language."""
import json

import tvserver
from test_http import HttpBase

# Default (no lang) texts: terse technical English, ASCII only.
LEGACY = {
    "invalid_device": "invalid device",
    "too_many_attempts": "too many failed login attempts",
    "too_many_requests": "try again in a moment",
    "pair_capacity_full": "try again in a moment",
    "body_not_object": "body must be a JSON object",
    "unknown_endpoint": "unknown endpoint",
    "preview_unavailable": "preview is not available",
    "invalid_install_id": "invalid installation identity",
    "invalid_pair_input": "invalid code or screen name",
    "pair_code_expired": "code has expired",
    "pair_code_used": "code has already been used",
    "state_save_failed": "state could not be saved",
    "hours_not_integer": "hours must be an integer",
    "order_not_list": "order must be a list of file names",
    "file_not_string": "file must be a string",
    "invalid_content_length": "invalid Content-Length",
    "request_too_large": "request is too large",
    "label_not_text": "label must be text",
    "label_invalid_chars": "label must not contain < > or control characters",
    "brand_not_object": "brand must be an object",
    "brand_url_invalid": "brand.url must be http(s), mailto or an e-mail address",
    "too_many_uploads": "too many uploads, try again in a moment",
    "invalid_idempotency_key": "invalid Idempotency-Key header",
    "upload_in_progress": "an upload with the same key is already in progress",
    "server_busy": "the server is busy with other uploads, try again in a moment",
    "upload_corrupt": "corrupted upload - data cannot be read",
    "empty_file": "empty file",
    "invalid_file_path": "invalid file path",
    "file_save_failed": "error while saving the file",
    "unauthorized": "invalid or missing credentials",
}
BAD = "a" * 49          # passes the route, but is not a valid device

PARAM = {
    "hours_out_of_range": ({"max": 168}, "hours must be 1 to 168"),
    "seconds_invalid": ({"max": 3600}, "seconds must be a number up to 3600"),
    "label_too_long": ({"max": 60}, "label is too long (max 60 characters)"),
    "brand_field_not_text": ({"field": "url"}, "brand.url must be text"),
    "brand_field_too_long": ({"field": "url"}, "brand.url is too long"),
    "brand_field_bad_chars": ({"field": "url"}, "brand.url contains disallowed characters"),
    "brand_field_not_email": ({"field": "email"}, "brand.email is not an e-mail address"),
    "media_bad_content": ({"ext": "jpg"}, "file content does not match the jpg format"),
    "media_bad_webm": ({}, "damaged or invalid webm file"),
    "media_bad_mp4": ({}, "invalid video file (MP4 container missing)"),
    "batch_too_large": ({"max": 5}, "batch is too large (max 5 MB)"),
    "file_too_large": ({"max": 5}, "file is too large (max 5 MB)"),
    "storage_quota": ({"max": 5}, "total storage capacity exceeded (max 5 MB)"),
    "item_limit": ({"max": 9}, "item limit per device reached (9)"),
    "unsupported_type": ({"allowed": ".jpg"}, "unsupported type (allowed: .jpg)"),
    "video_format_unsupported": ({"ext": "mkv"}, None),
    "heic_unsupported": ({}, None),
    "media_invalid": ({"reason": "xyz"}, "xyz"),
}


class TableTest(HttpBase):
    def test_every_code_has_default_cs_and_en(self):
        for code, t in tvserver.ERRORS.items():
            self.assertRegex(code, r"^[a-z][a-z0-9_]*$")
            for k in ("default", "cs", "en"):
                self.assertTrue(t.get(k), (code, k))
            self.assertNotEqual(t["cs"], t["en"], code)
            self.assertTrue(t["default"].isascii() or code == "upload_corrupt", code)

    def test_table_covers_tested_codes(self):
        for code in list(LEGACY) + list(PARAM):
            self.assertIn(code, tvserver.ERRORS)

    def test_default_texts(self):
        for code, text in LEGACY.items():
            self.assertEqual(tvserver.error_text(code), text, code)
            self.assertEqual(tvserver.ERRORS[code]["default"], text, code)
        for code, (params, text) in PARAM.items():
            got = tvserver.error_text(code, None, **params)
            if text:
                self.assertEqual(got, text, code)

    def test_params_in_both_languages(self):
        for code, (params, _) in PARAM.items():
            for lang in (None, "cs", "en"):
                got = tvserver.error_text(code, lang, **params)
                self.assertNotRegex(got, r"\{\w*\}", (code, lang))
                for k, v in params.items():
                    if k == "field" and lang:
                        continue        # the client sees a human-readable field name, not the key (test below)
                    self.assertIn(str(v), got, (code, lang))

    def test_en_is_english_without_czech_diacritics(self):
        for code in tvserver.ERRORS:
            if code == "media_invalid":
                continue
            self.assertTrue(tvserver.ERRORS[code]["en"].isascii(), code)

    def test_cs_has_diacritics_in_most(self):
        s = sum(1 for t in tvserver.ERRORS.values() if not t["cs"].isascii())
        self.assertGreater(s, len(tvserver.ERRORS) // 2)


# Field names as the client sees them in the admin ("Vzhled a kontakty").
BRAND_FIELDS = {
    "cs": {"name": "Název", "email": "E-mail", "phone": "Telefon", "city": "Město",
           "url": "Web / QR 1", "qrEmail": "E-mail pro QR 2", "pwaTitle": "Titulek aplikace"},
    "en": {"name": "Name", "email": "E-mail", "phone": "Phone", "city": "City",
           "url": "Website / QR 1", "qrEmail": "E-mail for QR 2", "pwaTitle": "App title"},
}
# Words the client must not see in cs/en texts: internal names, repo, terminal.
FORBIDDEN = ("ffmpeg", "convert-video", "repu", "repo", "brand", "moov", "mdat", "faststart",
            "Mpx", "Samsung", "DB10D", "mailto", "endpoint")
FORBIDDEN_CS = FORBIDDEN + ("upload", "zařízení", "heartbeat")
FORBIDDEN_EN = FORBIDDEN + ("device",)
# parameters sufficient for rendering (to test all codes at once)
SAMPLE_PARAMS = {"field": "url", "max": 5, "allowed": ".jpg", "ext": "mkv", "reason": "xyz",
            "w": 3840, "h": 2160, "mpx": 48.0, "max_mpx": 100, "max_side": 20000,
            "long": 1920, "short": 1080, "dev": "sal", "sw": 1280, "sh": 720, "mb": 6,
            "codec": "hev1", "codec_name": "H.265"}


class TextsByLanguageTest(HttpBase):
    def render(self, code, lang):
        import re as _re
        names = set(_re.findall(r"\{(\w+)", " ".join(tvserver.ERRORS[code].values())))
        names.discard("field_label")
        return tvserver.error_text(code, lang, **{k: SAMPLE_PARAMS[k] for k in names})

    def test_no_internal_name_or_repo_reference(self):
        for code in tvserver.ERRORS:
            for lang, bad in (("cs", FORBIDDEN_CS), ("en", FORBIDDEN_EN)):
                got = self.render(code, lang)
                for w in bad:
                    self.assertNotRegex(got.lower(), r"\b" + w.lower() + r"\b", (code, lang))

    def test_cs_and_en_start_with_capital(self):
        for code in tvserver.ERRORS:
            for lang in ("cs", "en"):
                t = tvserver.ERRORS[code][lang]
                self.assertTrue(t[0].isupper() or t[0] == "{", (code, lang, t))

    def test_en_no_ascii_hyphen_as_dash(self):
        for code, t in tvserver.ERRORS.items():
            self.assertNotIn(" - ", t["en"], code)
            self.assertNotIn(" - ", t["cs"], code)

    def test_email_in_en_consistently_hyphenated(self):
        for code, t in tvserver.ERRORS.items():
            self.assertNotRegex(t["en"], r"(?i)\bemail\b", code)

    def test_brand_errors_show_human_field_name(self):
        for code in ("brand_field_not_text", "brand_field_too_long",
                     "brand_field_bad_chars", "brand_field_not_email"):
            for lang in ("cs", "en"):
                for key, nazev in BRAND_FIELDS[lang].items():
                    got = tvserver.error_text(code, lang, field=key)
                    self.assertIn(nazev, got, (code, lang, key))
                    self.assertNotIn("brand", got.lower())
        for lang in ("cs", "en"):
            self.assertIn(BRAND_FIELDS[lang]["url"], tvserver.error_text("brand_url_invalid", lang))

    def test_brand_error_via_api_has_human_name(self):
        _, _, raw = self.req("POST", "/api/settings?lang=en",
                             {"device": "d", "brand": {"qrEmail": "x" * 200}})
        d = json.loads(raw)
        self.assertEqual(d["code"], "brand_field_too_long")
        self.assertIn("E-mail for QR 2", d["error"])
        _, _, raw = self.req("POST", "/api/settings",
                             {"device": "d", "brand": {"qrEmail": "x" * 200}})
        self.assertEqual(json.loads(raw)["error"], "brand.qrEmail is too long")

    def test_terms_screen_not_device(self):
        self.assertEqual(tvserver.error_text("invalid_device", "cs"), "Neplatná obrazovka")
        self.assertEqual(tvserver.error_text("invalid_device", "en"), "Invalid screen")
        self.assertIn("na obrazovku", tvserver.error_text("item_limit", "cs", max=30))
        self.assertIn("per screen", tvserver.error_text("item_limit", "en", max=30))

    def test_video_and_heic_advise_what_to_do(self):
        cs = tvserver.error_text("video_format_unsupported", "cs", ext="mkv")
        en = tvserver.error_text("video_format_unsupported", "en", ext="mkv")
        self.assertIn("MP4", cs)
        self.assertIn("H.264", cs)
        self.assertIn("mkv", cs)
        self.assertIn("MP4", en)
        self.assertIn("mkv", en)
        self.assertIn("JPG", tvserver.error_text("heic_unsupported", "cs"))
        self.assertIn("JPG", tvserver.error_text("heic_unsupported", "en"))

    def test_upload_naming_in_cs(self):
        for code in ("too_many_uploads", "upload_in_progress", "server_busy", "upload_corrupt"):
            self.assertNotIn("upload", tvserver.error_text(code, "cs").lower(), code)

    def test_media_codes_have_cs_en_and_default(self):
        for code in ("media_bad_content", "media_bad_webm", "media_bad_mp4", "media_quicktime",
                     "media_codec", "media_image_side", "media_image_huge",
                     "warn_video_big", "warn_video_over_screen", "warn_image_big",
                     "warn_image_over_screen", "warn_no_faststart",
                     "fix_repacked_quicktime", "fix_repacked_moov", "fix_repacked"):
            self.assertIn(code, tvserver.ERRORS)

    def test_resolution_and_dimensions_in_translation(self):
        got = tvserver.error_text("warn_video_big", "cs", w=3840, h=2160, long=1920, short=1080)
        self.assertIn("3840x2160", got)
        got = tvserver.error_text("warn_no_faststart", "en", mb=6)
        self.assertIn("6 MB", got)


class LanguagePickTest(HttpBase):
    def test_pick_lang(self):
        p = tvserver.pick_lang
        self.assertIsNone(p(None, None))
        self.assertIsNone(p(None, ""))
        self.assertEqual(p("en", None), "en")
        self.assertEqual(p("EN", None), "en")
        self.assertEqual(p("cs", "en"), "cs")          # ?lang= takes precedence
        self.assertEqual(p("en", "cs"), "en")
        self.assertEqual(p(None, "en-US,en;q=0.9"), "en")
        self.assertEqual(p(None, "cs-CZ,cs;q=0.9,en;q=0.5"), "cs")
        self.assertEqual(p(None, "de,en;q=0.5"), "en")
        self.assertEqual(p(None, "en;q=0.3, cs;q=0.8"), "cs")
        self.assertEqual(p(None, "en;q=0, cs;q=0.1"), "cs")
        self.assertIsNone(p(None, "de-DE,fr;q=0.8"))
        self.assertIsNone(p(None, "*"))
        self.assertIsNone(p("xx", None))               # unknown -> English default
        self.assertEqual(p("xx", "en"), "en")
        self.assertIsNone(p(None, ";;;q=,,,"))

    def test_error_text_unknown_language_is_default(self):
        self.assertEqual(tvserver.error_text("invalid_device", "de"), "invalid device")


class ApiErrorsTest(HttpBase):
    def get(self, path, headers=None, auth=True):
        st, hdr, raw = self.req("GET", path, None, headers, auth)
        return st, hdr, json.loads(raw)

    def test_default_error_unchanged_and_code(self):
        st, hdr, d = self.get("/api/tv/" + BAD + "/playlist", auth=False)
        self.assertEqual(st, 404)
        self.assertEqual(d, {"error": "invalid device", "code": "invalid_device"})
        self.assertEqual(hdr["Content-Language"], "en")

    def test_lang_en_query(self):
        st, hdr, d = self.get("/api/tv/" + BAD + "/playlist?lang=en", auth=False)
        self.assertEqual(d, {"error": "Invalid screen", "code": "invalid_device"})
        self.assertEqual(hdr["Content-Language"], "en")

    def test_accept_language_en(self):
        st, hdr, d = self.get("/api/tv/" + BAD + "/playlist", {"Accept-Language": "en-US,en;q=0.9"},
                              auth=False)
        self.assertEqual(d["error"], "Invalid screen")
        self.assertEqual(hdr["Content-Language"], "en")

    def test_query_takes_precedence_over_header(self):
        st, hdr, d = self.get("/api/tv/" + BAD + "/playlist?lang=cs", {"Accept-Language": "en"},
                              auth=False)
        self.assertEqual(d["error"], "Neplatná obrazovka")
        self.assertEqual(hdr["Content-Language"], "cs")

    def test_lang_cs_has_diacritics(self):
        st, hdr, d = self.get("/api/tv/" + BAD + "/playlist?lang=cs", auth=False)
        self.assertEqual(d["error"], "Neplatná obrazovka")
        self.assertEqual(d["code"], "invalid_device")

    def test_unknown_language_falls_back_to_default(self):
        for path, h in (("/api/tv/" + BAD + "/playlist?lang=xx", None),
                        ("/api/tv/" + BAD + "/playlist", {"Accept-Language": "de-DE,fr;q=0.8"})):
            st, hdr, d = self.get(path, h, auth=False)
            self.assertEqual(d["error"], "invalid device")
            self.assertEqual(hdr["Content-Language"], "en")

    def test_post_errors(self):
        cases = [
            ("/api/settings", {"device": "a.b"}, 400, "invalid_device"),
            ("/api/order", {"order": 5}, 400, "order_not_list"),
            ("/api/item", {"file": 5}, 400, "file_not_string"),
            ("/api/delete", {"file": 5}, 400, "file_not_string"),
            ("/api/item", {"file": "x", "seconds": 10 ** 9}, 400, "seconds_invalid"),
            ("/api/screens", {"label": "x" * 61}, 400, "label_too_long"),
            ("/api/screens", {"label": "<b>"}, 400, "label_invalid_chars"),
            ("/api/screens", {"label": 5}, 400, "label_not_text"),
            ("/api/settings", {"device": "d", "brand": 5}, 400, "brand_not_object"),
            ("/api/settings", {"device": "d", "brand": {"url": "javascript:x"}}, 400,
             "brand_url_invalid"),
            ("/api/pair/claim", {"code": "zz"}, 400, "invalid_pair_input"),
            ("/api/pair/claim", {"code": "ABCDEF", "device": "sal"}, 404, "pair_code_expired"),
            ("/api/pair/request", {"client": "x"}, 400, "invalid_install_id"),
            ("/api/neexistuje", {}, 404, "unknown_endpoint"),
        ]
        for path, body, status, code in cases:
            for q, hdrs, lang in (("", None, None), ("?lang=en", None, "en"),
                                  ("", {"Accept-Language": "en"}, "en"),
                                  ("?lang=cs", None, "cs")):
                with self.subTest(path=path, code=code, lang=lang):
                    st, hdr, raw = self.req("POST", path + q, body, hdrs)
                    d = json.loads(raw)
                    self.assertEqual(st, status)
                    self.assertEqual(d["code"], code)
                    tpl = tvserver.ERRORS[code]
                    key = lang or "default"
                    self.assertTrue(d["error"], d)
                    if "{" not in tpl[key]:
                        self.assertEqual(d["error"], tpl[key])
                    self.assertEqual(hdr["Content-Language"], lang or "en")

    def test_params_in_response(self):
        _, _, raw = self.req("POST", "/api/screens?lang=en", {"label": "x" * 61})
        self.assertEqual(json.loads(raw)["error"], "The label is too long (max 60 characters)")
        _, _, raw = self.req("POST", "/api/screens", {"label": "x" * 61})
        self.assertEqual(json.loads(raw)["error"], "label is too long (max 60 characters)")
        _, _, raw = self.req("GET", "/api/devices/x/availability?hours=999&lang=en")
        d = json.loads(raw)
        self.assertEqual(d["code"], "hours_out_of_range")
        self.assertIn(str(tvserver.AVAILABILITY_MAX_HOURS), d["error"])
        _, _, raw = self.req("GET", "/api/devices/x/availability?hours=abc")
        d = json.loads(raw)
        self.assertEqual((d["code"], d["error"]),
                         ("hours_not_integer", "hours must be an integer"))
        _, _, raw = self.req("GET", "/api/devices/" + BAD + "/availability?lang=en")
        self.assertEqual(json.loads(raw)["code"], "invalid_device")

    def test_user_label_is_not_translated(self):
        st, _, raw = self.req("POST", "/api/screens?lang=en", {"label": "Sál č. 1"})
        self.assertEqual(json.loads(raw)["label"], "Sál č. 1")

    def test_401_has_json_with_code_and_headers(self):
        for q, lang in (("", "en"), ("?lang=cs", "cs")):
            st, hdr, raw = self.req("POST", "/api/settings" + q, {}, auth=False)
            self.assertEqual(st, 401)
            self.assertIn("Basic", hdr["WWW-Authenticate"])
            d = json.loads(raw)
            self.assertEqual(d["code"], "unauthorized")
            self.assertEqual(hdr["Content-Language"], lang)
        self.assertEqual(hdr["Connection"].lower(), "close")

    def test_413_and_content_length(self):
        st, hdr, raw = self.req("POST", "/api/settings?lang=en", None,
                                {"Content-Length": str(10 * 1024 * 1024)})
        d = json.loads(raw)
        self.assertEqual((st, d["code"]), (413, "request_too_large"))
        self.assertEqual(d["error"], "The request is too large")
        st, hdr, raw = self.req("POST", "/api/settings", None, {"Content-Length": "abc"})
        self.assertEqual(json.loads(raw)["code"], "invalid_content_length")

    def test_pair_code_used(self):
        tvserver._state["pairs"]["ABCDEF"] = {"client": "c" * 20, "expires": 2 ** 31,
                                              "device": "sal"}
        st, hdr, raw = self.req("POST", "/api/pair/claim?lang=en",
                                {"code": "ABCDEF", "device": "sal"})
        d = json.loads(raw)
        self.assertEqual((st, d["code"]), (409, "pair_code_used"))
        self.assertEqual(d["error"], "The code has already been used")

    def test_thumb_unavailable(self):
        st, hdr, raw = self.req("GET", "/api/thumb/nic.jpg?lang=en")
        d = json.loads(raw)
        self.assertEqual((st, d["code"]), (404, "preview_unavailable"))


class UploadErrorsTest(HttpBase):
    def up(self, files, path="/api/upload?device=d", headers=None):
        body = b""
        for _, fname, data in files:
            body += (b"--XBX\r\nContent-Disposition: form-data; name=\"f\"; filename=\""
                     + fname.encode() + b"\"\r\nContent-Type: application/octet-stream\r\n\r\n"
                     + data + b"\r\n")
        body += b"--XBX--\r\n"
        h = {"Content-Type": "multipart/form-data; boundary=XBX"}
        h.update(headers or {})
        st, hdr, raw = self.req("POST", path, body, h)
        return st, hdr, json.loads(raw)

    def test_invalid_device_name_is_not_created(self):
        for dev in ("a%20b", "..%2Fx", "x" * 49):
            st, hdr, d = self.up([("file", "a.jpg", b"x")], "/api/upload?device=" + dev)
            self.assertEqual((st, d["code"]), (400, "invalid_device"), dev)
        self.assertNotIn("a b", tvserver._state.get("devices", {}))

    def test_unsupported_type_default_en_cs(self):
        st, hdr, d = self.up([("file", "a.xyz", b"x")])
        self.assertEqual(d["errors"][0], "a.xyz: " + tvserver.error_text(
            "unsupported_type", None, allowed=tvserver.ALLOWED_DESC))
        self.assertTrue(d["errors"][0].startswith("a.xyz: unsupported type (allowed: "))
        self.assertEqual(d["error_codes"], ["unsupported_type"])
        self.assertEqual(d["code"], "unsupported_type")
        self.assertEqual(hdr["Content-Language"], "en")
        st, hdr, d = self.up([("file", "a.xyz", b"x")], "/api/upload?device=d&lang=en")
        self.assertTrue(d["errors"][0].startswith("a.xyz: This file type is not supported (allowed: "))
        self.assertEqual(hdr["Content-Language"], "en")
        st, hdr, d = self.up([("file", "a.xyz", b"x")], headers={"Accept-Language": "en"})
        self.assertIn("not supported", d["errors"][0])

    def test_filename_is_not_translated(self):
        st, hdr, d = self.up([("file", "Šíleně dlouhý.mkv", b"x")],
                             "/api/upload?device=d&lang=en")
        self.assertTrue(d["errors"][0].startswith("Šíleně dlouhý.mkv: "))
        self.assertEqual(d["error_codes"], ["video_format_unsupported"])

    def test_heic_and_empty(self):
        st, _, d = self.up([("file", "a.heic", b"x"), ("file", "b.jpg", b"")],
                           "/api/upload?device=d&lang=en")
        self.assertEqual(d["error_codes"], ["heic_unsupported", "empty_file"])
        self.assertTrue(d["errors"][1].endswith("The file is empty"))
        self.assertFalse(d["ok"])

    def test_invalid_idempotency_header(self):
        st, hdr, d = self.up([("file", "a.jpg", b"x")], "/api/upload?device=d&lang=en",
                             {"Idempotency-Key": "%%%"})
        self.assertEqual(st, 400)
        self.assertEqual(d["code"], "invalid_idempotency_key")
        self.assertEqual(d["error_codes"], ["invalid_idempotency_key"])
        self.assertEqual(d["errors"], ["Invalid Idempotency-Key header"])

    def test_broken_multipart(self):
        st, hdr, raw = self.req("POST", "/api/upload?lang=en", b"zzz",
                                {"Content-Type": "multipart/form-data; boundary=x"})
        d = json.loads(raw)
        self.assertEqual(st, 400)
        self.assertEqual(d["code"], "upload_corrupt")
        self.assertEqual(hdr["Content-Language"], "en")

    def test_quicktime_cs_en_default(self):
        import test_tvserver as t
        data = t.qtmov(b"avc1")
        st, _, d = self.up([("f", "a.mov", data)], "/api/upload?device=d&lang=en")
        self.assertEqual(d["error_codes"], ["media_invalid"])
        self.assertIn("QuickTime", d["errors"][0])
        self.assertIn("MP4", d["errors"][0])
        self.assertNotIn("ffmpeg", d["errors"][0])
        st, _, d = self.up([("f", "a.mov", data)], "/api/upload?device=d&lang=cs")
        self.assertIn("Soubor nelze použít", d["errors"][0])
        self.assertNotIn("ffmpeg", d["errors"][0])
        st, _, d = self.up([("f", "a.mov", data)])
        self.assertIn("ffmpeg -i input.mov", d["errors"][0])      # default unchanged
        self.assertTrue(d["errors"][0].isascii())

    def test_warnings_in_response_language(self):
        import test_tvserver as t
        data = t.mp4(b"avc1", 3840, 2160)
        st, _, d = self.up([("f", "a.mp4", data)], "/api/upload?device=d&lang=en")
        self.assertEqual(len(d["warnings"]), 1, d)
        self.assertNotIn("Samsung", d["warnings"][0])
        self.assertIn("3840x2160", d["warnings"][0])
        self.assertIn("older", d["warnings"][0])
        st, _, d = self.up([("f", "b.mp4", data)], "/api/upload?device=d&lang=cs")
        self.assertIn("starších", d["warnings"][0])
        st, _, d = self.up([("f", "c.mp4", data)])
        self.assertIn("Samsung DB10D", d["warnings"][0])       # default unchanged
