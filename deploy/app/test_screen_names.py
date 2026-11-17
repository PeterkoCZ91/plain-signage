"""T2: unguessable screen names, label, enumeration, media names."""
import json

import tvserver
from test_http import HttpBase, multipart_body


class NameTest(HttpBase):
    def test_name_is_valid_unique_and_unguessable(self):
        names = {tvserver.new_screen_name("Recepce") for _ in range(200)}
        self.assertEqual(len(names), 200)
        for n in names:
            self.assertTrue(tvserver.valid_device(n))
            self.assertRegex(n, r"^recepce-[0-9a-f]{12}$")

    def test_slug_from_label_is_safe(self):
        for label in ("", "   ", "<b>Ěšč ř</b>", "../../x", "x" * 300):
            n = tvserver.new_screen_name(label)
            self.assertTrue(tvserver.valid_device(n), n)
            self.assertNotEqual(n, "default")

    def test_name_does_not_collide_with_existing(self):
        with mock_token(["aaaaaaaaaaaa", "bbbbbbbbbbbb"]):
            tvserver._state["devices"]["screen-aaaaaaaaaaaa"] = {"version": 0, "items": []}
            self.assertEqual(tvserver.new_screen_name("", taken=tvserver._state["devices"]),
                             "screen-bbbbbbbbbbbb")


class mock_token:
    def __init__(self, vals):
        self.vals = list(vals)

    def __enter__(self):
        self.orig = tvserver.secrets.token_hex
        tvserver.secrets.token_hex = lambda n=None: self.vals.pop(0)

    def __exit__(self, *a):
        tvserver.secrets.token_hex = self.orig


class ScreensApiTest(HttpBase):
    def create(self, body):
        st, _, raw = self.req("POST", "/api/screens", body)
        return st, json.loads(raw)

    def test_requires_login(self):
        st, _, _ = self.req("POST", "/api/screens", {"label": "A"}, auth=False)
        self.assertEqual(st, 401)

    def test_creates_screen_with_label(self):
        st, d = self.create({"label": "Recepce  1"})
        self.assertEqual(st, 200)
        self.assertTrue(tvserver.valid_device(d["device"]))
        self.assertEqual(d["label"], "Recepce 1")
        stored = tvserver._state["devices"][d["device"]]
        self.assertEqual(stored["settings"]["label"], "Recepce 1")

    def test_without_label_also_passes(self):
        st, d = self.create({})
        self.assertEqual(st, 200)
        self.assertTrue(tvserver.valid_device(d["device"]))

    def test_label_with_html_is_rejected(self):
        for bad in ("<script>", "a<b", "x>y", "a\x00b", 5):
            with self.subTest(bad=bad):
                st, _ = self.create({"label": bad})
                self.assertEqual(st, 400)
        self.assertEqual(tvserver._state["devices"], {})

    def test_label_length_is_limited(self):
        st, _ = self.create({"label": "x" * 61})
        self.assertEqual(st, 400)

    def test_label_via_settings_and_listing(self):
        _, d = self.create({"label": "Sál"})
        dev = d["device"]
        st, _, _ = self.req("POST", "/api/settings", {"device": dev, "label": "Nový sál"})
        self.assertEqual(st, 200)
        st, _, raw = self.req("GET", "/api/devices")
        self.assertEqual(json.loads(raw)["devices"][dev]["label"], "Nový sál")
        st, _, _ = self.req("POST", "/api/settings", {"device": dev, "label": "<i>"})
        self.assertEqual(st, 400)
        st, _, _ = self.req("POST", "/api/settings", {"device": dev, "label": ""})
        self.assertEqual(st, 200)
        self.assertNotIn("label", tvserver._state["devices"][dev]["settings"])

    def test_label_not_in_public_playlist(self):
        _, d = self.create({"label": "Tajný popisek"})
        st, _, raw = self.req("GET", f"/api/tv/{d['device']}/playlist", auth=False)
        self.assertEqual(st, 200)
        self.assertNotIn("Tajný", raw.decode())

    def test_legacy_device_without_label_has_empty_label(self):
        tvserver._state["devices"]["default"] = {"version": 1, "items": []}
        _, _, raw = self.req("GET", "/api/devices")
        self.assertEqual(json.loads(raw)["devices"]["default"]["label"], "")

    def test_claim_without_name_creates_random(self):
        tvserver._state["pairs"]["ABCDEF"] = {"client": "c" * 20, "expires": 2 ** 31}
        st, _, raw = self.req("POST", "/api/pair/claim", {"code": "ABCDEF", "label": "Hala"})
        self.assertEqual(st, 200)
        dev = json.loads(raw)["device"]
        self.assertRegex(dev, r"^hala-[0-9a-f]{12}$")
        self.assertEqual(tvserver._state["pairs"]["ABCDEF"]["device"], dev)

    def test_claim_with_name_stays_unchanged(self):
        tvserver._state["pairs"]["ABCDEF"] = {"client": "c" * 20, "expires": 2 ** 31}
        st, _, _ = self.req("POST", "/api/pair/claim", {"code": "ABCDEF", "device": "sal"})
        self.assertEqual(st, 200)
        self.assertIn("sal", tvserver._state["devices"])


class ExistingDevicesTest(HttpBase):
    def test_default_and_legacy_names_work(self):
        for name in ("default", "sal", "tv_1"):
            tvserver._state["devices"][name] = {"version": 3, "items": []}
            st, _, raw = self.req("GET", f"/api/tv/{name}/playlist", auth=False)
            self.assertEqual(st, 200)
            self.assertEqual(json.loads(raw)["device"], name)
            self.assertEqual(json.loads(raw)["version"], 3)
        st, _, _ = self.req("GET", "/tv/", auth=False)
        self.assertEqual(st, 200)
        self.assertIn("default", tvserver._state["devices"])


class EnumerationTest(HttpBase):
    def test_known_and_unknown_are_indistinguishable(self):
        tvserver._state["devices"]["znama"] = {"version": 0, "items": []}
        st1, h1, b1 = self.req("GET", "/api/tv/znama/playlist", auth=False)
        st2, h2, b2 = self.req("GET", "/api/tv/neznama/playlist", auth=False)
        self.assertEqual(st1, st2)
        j1, j2 = json.loads(b1), json.loads(b2)
        j1.pop("device"), j2.pop("device")
        self.assertEqual(j1, j2)
        self.assertEqual(h1.get("Cache-Control"), h2.get("Cache-Control"))

    def test_heartbeat_of_unknown_creates_nothing(self):
        self.req("GET", "/api/tv/neznama/playlist?heartbeat=1", auth=False)
        self.assertNotIn("neznama", tvserver._state["devices"])
        self.assertNotIn("neznama", tvserver._online)

    def test_qr_does_not_reveal_existence(self):
        tvserver._state["devices"]["znama"] = {"version": 1, "items": []}
        a = self.req("GET", "/qr.png?device=znama", auth=False)
        b = self.req("GET", "/qr.png?device=neznama", auth=False)
        self.assertEqual(a[0], b[0])
        self.assertEqual(a[2], b[2])


class MediaNamesTest(HttpBase):
    def upload(self, dev, name="logo.png"):
        png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
               b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
               b"\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa0\x00\x00\x00\x00IEND\xaeB`\x82")
        body, hdr = multipart_body(name, png)
        st, _, raw = self.req("POST", f"/api/upload?device={dev}", body,
                              hdr)
        return st, json.loads(raw)

    def test_new_upload_has_random_prefix_and_extension_is_kept(self):
        st, d = self.upload("d1")
        self.assertEqual(st, 200, d)
        a = d["saved"][0]
        self.assertRegex(a, r"^[0-9a-f]{16}_logo\.png$")
        _, d2 = self.upload("d1")
        self.assertNotEqual(a, d2["saved"][0])
        st, _, _ = self.req("GET", f"/media/{a}", auth=False)
        self.assertEqual(st, 200)
        st, _, _ = self.req("GET", "/media/logo.png", auth=False)
        self.assertEqual(st, 404)

    def test_legacy_items_with_old_name_are_still_served(self):
        import os
        with open(os.path.join(tvserver.MEDIA, "stary.png"), "wb") as f:
            f.write(b"x")
        tvserver._state["devices"]["d1"] = {"version": 1, "items": [
            {"id": "stary.png", "file": "stary.png", "type": "image"}]}
        st, _, _ = self.req("GET", "/media/stary.png", auth=False)
        self.assertEqual(st, 200)
        _, _, raw = self.req("GET", "/api/tv/d1/playlist", auth=False)
        self.assertEqual(json.loads(raw)["items"][0]["url"], "/media/stary.png")


class ScreenQrTest(HttpBase):
    def test_player_qr_is_protected_png(self):
        st, _, _ = self.req("GET", "/api/screens/sal-abc/qr.png", auth=False)
        self.assertEqual(st, 401)
        st, hdr, body = self.req("GET", "/api/screens/sal-abc/qr.png")
        if tvserver.segno is None:
            self.assertEqual(st, 503)
        else:
            self.assertEqual(st, 200)
            self.assertEqual(hdr.get("Content-Type"), "image/png")
            self.assertTrue(body.startswith(b"\x89PNG"))
