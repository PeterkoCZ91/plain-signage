"""Controlled Android kiosk update: release on disk, public poll, APK, admin request."""
import hashlib
import json
import os
from unittest import mock

import tvserver
from test_http import HttpBase

APK = b"PK\x03\x04 falesne apk pro testy " * 64


def publish(code=7, name="1.9", data=APK, **override):
    """Write a release into KIOSK_DIR (like publish-kiosk.sh)."""
    os.makedirs(tvserver.KIOSK_DIR, exist_ok=True)
    with open(os.path.join(tvserver.KIOSK_DIR, tvserver.KIOSK_APK), "wb") as fh:
        fh.write(data)
    meta = {"versionCode": code, "versionName": name,
            "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    meta.update(override)
    write_json(meta)
    return meta


def write_json(meta):
    os.makedirs(tvserver.KIOSK_DIR, exist_ok=True)
    raw = meta if isinstance(meta, str) else json.dumps(meta)
    with open(os.path.join(tvserver.KIOSK_DIR, tvserver.KIOSK_JSON), "w") as fh:
        fh.write(raw)


class ReleaseValidationTest(HttpBase):
    def test_no_files_no_release(self):
        self.assertIsNone(tvserver.kiosk_release())
        st, _, raw = self.req("GET", "/api/kiosk/release", auth=False)
        self.assertEqual(st, 404)
        self.assertEqual(json.loads(raw)["code"], "no_kiosk_release")

    def test_json_without_apk(self):
        publish()
        os.remove(os.path.join(tvserver.KIOSK_DIR, tvserver.KIOSK_APK))
        self.assertIsNone(tvserver.kiosk_release())

    def test_valid_release(self):
        meta = publish()
        self.assertEqual(tvserver.kiosk_release(), meta)

    def test_bad_json(self):
        publish()
        write_json("{nejde to")
        self.assertIsNone(tvserver.kiosk_release())

    def test_size_mismatch(self):
        publish(size=len(APK) + 1)
        self.assertIsNone(tvserver.kiosk_release())
        st, _, _ = self.req("GET", tvserver.KIOSK_URL, auth=False)
        self.assertEqual(st, 404)

    def test_invalid_fields(self):
        for bad in ({"versionCode": 0}, {"versionCode": "7"}, {"versionCode": True},
                    {"versionName": ""}, {"versionName": "x" * 33}, {"versionName": 5},
                    {"sha256": "AB" * 32}, {"sha256": "ab" * 31}, {"size": -1}):
            with self.subTest(bad=bad):
                publish(**bad)
                self.assertIsNone(tvserver.kiosk_release())
        publish(versionName="x" * 32)
        self.assertIsNotNone(tvserver.kiosk_release())

    def test_file_change_takes_effect(self):
        publish(code=7)
        self.assertEqual(tvserver.kiosk_release()["versionCode"], 7)
        publish(code=8, data=APK + b"x")
        self.assertEqual(tvserver.kiosk_release()["versionCode"], 8)


class PublicEndpointsTest(HttpBase):
    def test_release_without_login(self):
        meta = publish()
        st, hdr, raw = self.req("GET", "/api/kiosk/release", auth=False)
        self.assertEqual(st, 200)
        d = json.loads(raw)
        self.assertEqual(d, {**meta, "url": "/kiosk/signage-kiosk.apk", "requested": False})
        self.assertEqual(hdr["Cache-Control"], "no-store")

    def test_apk_without_login(self):
        publish()
        st, hdr, raw = self.req("GET", "/kiosk/signage-kiosk.apk", auth=False)
        self.assertEqual(st, 200)
        self.assertEqual(raw, APK)
        self.assertEqual(hdr["Content-Type"], "application/vnd.android.package-archive")
        self.assertEqual(hdr["Cache-Control"], "no-cache")
        st, hdr, raw = self.req("HEAD", "/kiosk/signage-kiosk.apk", auth=False)
        self.assertEqual((st, raw), (200, b""))

    def test_apk_without_release_404(self):
        st, _, _ = self.req("GET", "/kiosk/signage-kiosk.apk", auth=False)
        self.assertEqual(st, 404)

    def test_other_name_and_traversal(self):
        publish()
        for path in ("/kiosk/kiosk.json", "/kiosk/", "/kiosk/../kiosk/kiosk.json",
                     "/kiosk/%2e%2e/tv-state.json", "/kiosk/signage-kiosk.apk/x"):
            with self.subTest(path=path):
                st, _, _ = self.req("GET", path, auth=False)
                self.assertEqual(st, 404)

    def test_not_protected(self):
        for p in ("/api/kiosk/release", tvserver.KIOSK_URL):
            self.assertFalse(any(p == x or p.startswith(x) for x in tvserver.PROTECTED), p)
        self.assertTrue(any("/api/screens/x/kiosk-update".startswith(x) for x in tvserver.PROTECTED))

    def test_rate_limit_one_poll_per_minute_passes(self):
        tvserver.reset_rate_limits()
        try:
            for _ in range(30):            # 30 boxes behind one IP within one minute
                st, _, _ = self.req("GET", "/api/kiosk/release", auth=False)
                self.assertEqual(st, 404)
            with mock.patch.object(tvserver._kiosk_limiter, "limit", 30):
                st, hdr, raw = self.req("GET", "/api/kiosk/release", auth=False)
            self.assertEqual(st, 429)
            self.assertIn("Retry-After", hdr)
        finally:
            tvserver.reset_rate_limits()


class RequestTest(HttpBase):
    def setUp(self):
        super().setUp()
        tvserver.reset_rate_limits()
        tvserver._state["devices"]["sal"] = {"version": 3, "items": []}

    def poll(self, **q):
        qs = "&".join(f"{k}={v}" for k, v in q.items())
        st, _, raw = self.req("GET", "/api/kiosk/release?" + qs, auth=False)
        return st, json.loads(raw)

    def request(self, dev="sal", body=None):
        st, _, raw = self.req("POST", f"/api/screens/{dev}/kiosk-update", body or {})
        return st, json.loads(raw)

    def test_cycle_request_and_settle_install(self):
        publish(code=7)
        st, d = self.request()
        self.assertEqual((st, d), (200, {"ok": True, "requested": 7}))
        self.assertEqual(tvserver._state["devices"]["sal"]["kiosk_update"], 7)
        self.assertEqual(tvserver._state["devices"]["sal"]["version"], 3)   # playlist is unchanged
        with open(tvserver.STATE) as fh:
            self.assertEqual(json.load(fh)["devices"]["sal"]["kiosk_update"], 7)
        with mock.patch.object(tvserver, "save") as save:
            st, d = self.poll(device="sal", have=6, state="downloading")
            self.assertEqual((st, d["requested"]), (200, True))
            st, d = self.poll(device="sal", have=6, state="waiting")
            self.assertTrue(d["requested"])
            save.assert_not_called()           # a poll without change does not write
        self.assertEqual(tvserver._online["sal"]["kiosk_code"], 6)
        self.assertEqual(tvserver._online["sal"]["kiosk_state"], "waiting")
        st, d = self.poll(device="sal", have=7, state="installed")
        self.assertFalse(d["requested"])
        self.assertNotIn("kiosk_update", tvserver._state["devices"]["sal"])
        with open(tvserver.STATE) as fh:
            self.assertNotIn("kiosk_update", json.load(fh)["devices"]["sal"])

    def test_declined_failed_no_permission_settle(self):
        publish(code=7)
        for state in ("declined", "failed", "no_permission"):
            with self.subTest(state=state):
                self.request()
                st, d = self.poll(device="sal", have=6, state="downloading")
                self.assertTrue(d["requested"])
                st, d = self.poll(device="sal", have=6, state=state)
                self.assertFalse(d["requested"])
                self.assertNotIn("kiosk_update", tvserver._state["devices"]["sal"])

    def test_stale_result_does_not_cancel_new_request(self):
        # the box keeps sending its last result; the first poll after a request ignores it
        publish(code=7)
        self.poll(device="sal", have=6, state="declined")
        self.request()
        st, d = self.poll(device="sal", have=6, state="declined")
        self.assertTrue(d["requested"])
        self.assertEqual(tvserver._state["devices"]["sal"]["kiosk_update"], 7)
        # the box tried again and nobody confirmed again -> settled
        st, d = self.poll(device="sal", have=6, state="declined")
        self.assertFalse(d["requested"])
        self.assertNotIn("kiosk_update", tvserver._state["devices"]["sal"])

    def test_install_settles_even_on_first_poll(self):
        publish(code=7)
        self.request()
        st, d = self.poll(device="sal", have=7, state="installed")
        self.assertFalse(d["requested"])

    def test_invalid_values_are_ignored(self):
        publish(code=7)
        self.poll(device="sal", have=5, state="idle")
        st, d = self.poll(device="sal", have="abc", state="hack<>")
        self.assertEqual(st, 200)
        self.assertEqual(tvserver._online["sal"]["kiosk_code"], 5)
        self.assertEqual(tvserver._online["sal"]["kiosk_state"], "idle")
        self.poll(device="sal", have="-3")
        self.poll(device="sal", have="99999999999")
        self.assertEqual(tvserver._online["sal"]["kiosk_code"], 5)

    def test_unknown_screen_writes_nothing(self):
        publish(code=7)
        with mock.patch.object(tvserver, "save") as save:
            for dev in ("neznamy", "a.b", "x" * 60):
                st, d = self.poll(device=dev, have=1, state="failed")
                self.assertEqual((st, d["requested"]), (200, False))
            save.assert_not_called()
        self.assertEqual(tvserver._online, {})
        self.assertNotIn("neznamy", tvserver._state["devices"])
        self.assertFalse(os.path.exists(tvserver.STATE))

    def test_player_heartbeat_keeps_kiosk_fields(self):
        publish(code=7)
        self.poll(device="sal", have=6, state="idle")
        self.req("GET", "/api/tv/sal/playlist?heartbeat=1&ok=1", auth=False)
        self.assertEqual(tvserver._online["sal"]["kiosk_code"], 6)
        self.assertEqual(tvserver._online["sal"]["kiosk_state"], "idle")

    def test_post_nonexistent_screen(self):
        publish(code=7)
        st, d = self.request("neznamy")
        self.assertEqual((st, d["code"]), (404, "invalid_device"))
        st, d = self.request("a-b%2E")          # outside the name pattern -> path does not exist
        self.assertEqual(st, 404)
        self.assertNotIn("neznamy", tvserver._state["devices"])

    def test_post_requires_login(self):
        publish(code=7)
        st, _, _ = self.req("POST", "/api/screens/sal/kiosk-update", {}, auth=False)
        self.assertEqual(st, 401)

    def test_post_without_release(self):
        st, d = self.request()
        self.assertEqual((st, d["code"]), (409, "no_kiosk_release"))

    def test_up_to_date(self):
        publish(code=7)
        self.poll(device="sal", have=7)
        st, d = self.request()
        self.assertEqual((st, d["code"]), (409, "kiosk_up_to_date"))
        self.assertNotIn("kiosk_update", tvserver._state["devices"]["sal"])

    def test_cancel(self):
        publish(code=7)
        self.request()
        st, d = self.request(body={"cancel": True})
        self.assertEqual((st, d), (200, {"ok": True, "requested": None}))
        self.assertNotIn("kiosk_update", tvserver._state["devices"]["sal"])
        st, d = self.poll(device="sal", have=6)
        self.assertFalse(d["requested"])
        # cancelling without a request is a no-op
        st, d = self.request(body={"cancel": True})
        self.assertEqual((st, d), (200, {"ok": True, "requested": None}))

    def test_failed_save_restores_previous_state(self):
        publish(code=7)
        with mock.patch.object(tvserver, "save", side_effect=OSError("disk")):
            st, d = self.request()
        self.assertEqual((st, d["code"]), (500, "state_save_failed"))
        self.assertNotIn("kiosk_update", tvserver._state["devices"]["sal"])

    def test_list_devices_fields(self):
        st, _, raw = self.req("GET", "/api/devices")
        d = json.loads(raw)
        self.assertIsNone(d["kiosk_release"])
        sal = d["devices"]["sal"]
        self.assertEqual((sal["kiosk_code"], sal["kiosk_state"], sal["kiosk_update"]),
                         (None, "", None))
        publish(code=7, name="1.9")
        self.poll(device="sal", have=6, state="waiting")
        self.request()
        d = json.loads(self.req("GET", "/api/devices")[2])
        self.assertEqual(d["kiosk_release"], {"versionCode": 7, "versionName": "1.9"})
        sal = d["devices"]["sal"]
        self.assertEqual((sal["kiosk_code"], sal["kiosk_state"], sal["kiosk_update"]),
                         (6, "waiting", 7))

    def test_flag_survives_load_and_invalid_is_dropped(self):
        raw = {"devices": {"a": {"items": [], "kiosk_update": 7},
                           "b": {"items": [], "kiosk_update": "7"},
                           "c": {"items": [], "kiosk_update": True}}}
        out = tvserver._normalize_state(raw)["devices"]
        self.assertEqual(out["a"]["kiosk_update"], 7)
        self.assertNotIn("kiosk_update", out["b"])
        self.assertNotIn("kiosk_update", out["c"])


class BoxHealthTest(HttpBase):
    """Box health in the same poll: RAM, uptime (restarts), Wi-Fi, renderer crashes."""

    def setUp(self):
        super().setUp()
        tvserver.reset_rate_limits()
        tvserver._state["devices"]["sal"] = {"version": 3, "items": []}
        publish(code=7)

    def poll(self, **q):
        qs = "&".join(f"{k}={v}" for k, v in q.items())
        st, _, raw = self.req("GET", "/api/kiosk/release?" + qs, auth=False)
        return st

    def sal(self):
        return json.loads(self.req("GET", "/api/devices")[2])["devices"]["sal"]

    def test_valid_values_are_stored(self):
        st = self.poll(device="sal", have=7, state="idle", ram=412, ram_low=0,
                       ram_total=1838, up=3600, rssi=-58, net="wifi", crash=2)
        self.assertEqual(st, 200)
        e = tvserver._online["sal"]
        self.assertEqual((e["kiosk_ram"], e["kiosk_ram_low"], e["kiosk_ram_total"],
                          e["kiosk_up"], e["kiosk_rssi"], e["kiosk_net"], e["kiosk_crash"]),
                         (412, False, 1838, 3600, -58, "wifi", 2))
        self.assertEqual(e["kiosk_boots"], 0)
        self.assertAlmostEqual(e["kiosk_last_boot"], int(tvserver.time.time()) - 3600, delta=2)

    def test_limits_and_invalid_are_ignored(self):
        cases = {"ram": ("-1", "65537", "abc", "1.5", "", "0x10"),
                 "ram_total": ("-5", "70000"),
                 "up": ("-1", "1000000001", "1e9"),
                 "rssi": ("-128", "1", "5", "--3"),
                 "crash": ("-1", "1000001", "x")}
        for key, bads in cases.items():
            for bad in bads:
                with self.subTest(key=key, bad=bad):
                    self.assertEqual(self.poll(device="sal", net="eth", **{key: bad}), 200)
                    self.assertIsNone(tvserver._online["sal"]["kiosk_" + key])
        for key, ok in (("ram", "0"), ("ram", "65536"), ("up", "1000000000"),
                        ("rssi", "-127"), ("rssi", "0"), ("crash", "1000000")):
            with self.subTest(key=key, ok=ok):
                self.poll(device="sal", net="wifi", **{key: ok})
                self.assertEqual(tvserver._online["sal"]["kiosk_" + key], int(ok))
        self.poll(device="sal", ram_low="ano", net="lte", ram=5)
        e = tvserver._online["sal"]
        self.assertEqual((e["kiosk_ram_low"], e["kiosk_net"], e["kiosk_ram"]), (None, None, 5))
        self.poll(device="sal", ram_low=1, net="other")
        self.assertEqual((tvserver._online["sal"]["kiosk_ram_low"],
                          tvserver._online["sal"]["kiosk_net"]), (True, "other"))

    def test_rssi_only_for_wifi(self):
        self.poll(device="sal", rssi=-60, net="eth")
        self.assertIsNone(tvserver._online["sal"]["kiosk_rssi"])
        self.poll(device="sal", rssi=-60, net="wifi")
        self.assertEqual(tvserver._online["sal"]["kiosk_rssi"], -60)
        # switched to cable: the kiosk sends no rssi -> the old value does not linger
        self.poll(device="sal", net="eth")
        self.assertIsNone(tvserver._online["sal"]["kiosk_rssi"])

    def test_older_kiosk_without_health_erases_nothing(self):
        self.poll(device="sal", ram=300, up=100)
        self.poll(device="sal", have=7, state="idle")      # an older client sends none of this
        self.assertEqual(tvserver._online["sal"]["kiosk_ram"], 300)
        self.assertEqual(tvserver._online["sal"]["kiosk_up"], 100)

    def test_restart_from_uptime_drop(self):
        with mock.patch.object(tvserver.time, "time", return_value=10_000):
            self.poll(device="sal", up=5000)
        e = tvserver._online["sal"]
        self.assertEqual((e["kiosk_boots"], e["kiosk_last_boot"]), (0, 5000))
        with mock.patch.object(tvserver.time, "time", return_value=10_060):
            self.poll(device="sal", up=5060)
        self.assertEqual((e["kiosk_boots"], e["kiosk_last_boot"]), (0, 5000))
        with mock.patch.object(tvserver.time, "time", return_value=10_200):
            self.poll(device="sal", up=40)                  # the box has powered on again in the meantime
        self.assertEqual((e["kiosk_boots"], e["kiosk_last_boot"]), (1, 10_160))
        with mock.patch.object(tvserver.time, "time", return_value=10_260):
            self.poll(device="sal", up=100)
            self.poll(device="sal", up="nesmysl")            # invalid: not a restart
            self.poll(device="sal", up=160)
        self.assertEqual(e["kiosk_boots"], 1)
        with mock.patch.object(tvserver.time, "time", return_value=11_000):
            self.poll(device="sal", up=10)
        self.assertEqual((e["kiosk_boots"], e["kiosk_last_boot"]), (2, 10_990))
        self.assertEqual((self.sal()["kiosk_boots"], self.sal()["kiosk_last_boot"]), (2, 10_990))

    def test_nothing_on_disk(self):
        with mock.patch.object(tvserver, "save") as save:
            for _ in range(3):
                self.poll(device="sal", ram=50, up=10, rssi=-80, net="wifi", crash=1)
            save.assert_not_called()
        self.assertFalse(os.path.exists(tvserver.STATE))
        self.assertFalse(os.path.exists(tvserver.TELEMETRY))

    def test_unknown_and_invalid_screen_writes_nothing(self):
        with mock.patch.object(tvserver, "save") as save:
            for dev in ("neznamy", "a.b", "x" * 60, ""):
                self.assertEqual(self.poll(device=dev, ram=50, up=10, crash=1), 200)
            save.assert_not_called()
        self.assertEqual(tvserver._online, {})
        self.assertNotIn("neznamy", tvserver._state["devices"])

    def test_player_heartbeat_keeps_health(self):
        self.poll(device="sal", ram=300, up=100, rssi=-70, net="wifi", crash=1)
        self.poll(device="sal", up=5)
        self.req("GET", "/api/tv/sal/playlist?heartbeat=1&ok=1", auth=False)
        e = tvserver._online["sal"]
        self.assertEqual((e["kiosk_up"], e["kiosk_boots"], e["kiosk_net"]), (5, 1, None))
        self.assertIn("kiosk_seen", e)

    def test_list_devices_health_fields(self):
        sal = self.sal()
        self.assertEqual({k: sal[k] for k in (
            "kiosk_ram", "kiosk_ram_low", "kiosk_ram_total", "kiosk_up", "kiosk_boots",
            "kiosk_last_boot", "kiosk_rssi", "kiosk_net", "kiosk_crash", "kiosk_seen")},
            {"kiosk_ram": None, "kiosk_ram_low": None, "kiosk_ram_total": None,
             "kiosk_up": None, "kiosk_boots": 0, "kiosk_last_boot": None,
             "kiosk_rssi": None, "kiosk_net": "", "kiosk_crash": None, "kiosk_seen": None})
        self.poll(device="sal", ram=95, ram_low=1, ram_total=2000, up=60,
                  rssi=-81, net="wifi", crash=0)
        sal = self.sal()
        self.assertEqual((sal["kiosk_ram"], sal["kiosk_ram_low"], sal["kiosk_ram_total"],
                          sal["kiosk_up"], sal["kiosk_rssi"], sal["kiosk_net"], sal["kiosk_crash"]),
                         (95, True, 2000, 60, -81, "wifi", 0))
        self.assertIsInstance(sal["kiosk_seen"], int)


class LowRamAlertTest(HttpBase):
    """Alert on persistently low RAM: 3 polls in a row under 80 MB, recovery only above 120 MB."""

    def setUp(self):
        super().setUp()
        tvserver.reset_rate_limits()
        tvserver._state["devices"]["sal"] = {"version": 3, "items": []}

    def poll_round(self, ram, alerted):
        self.req("GET", f"/api/kiosk/release?device=sal&ram={ram}", auth=False)
        online = {"sal": dict(tvserver._online["sal"], last_seen=10_000)}
        return tvserver.alert_events(online, alerted, 10_030, 600)

    def test_three_polls_in_a_row(self):
        al = set()
        for ram in (50, 60):
            ev, al = self.poll_round(ram, al)
            self.assertEqual(ev, [])
        ev, al = self.poll_round(300, al)                        # streak interrupted
        ev, al = self.poll_round(50, al)
        ev, al = self.poll_round(50, al)
        self.assertEqual(ev, [])
        ev, al = self.poll_round(79, al)
        self.assertEqual(ev, [("low_ram", "sal", 10_000)])
        ev, al = self.poll_round(40, al)                         # already reported: nothing again
        self.assertEqual(ev, [])
        ev, al = self.poll_round(100, al)                        # hysteresis: not "ok" yet
        self.assertEqual(ev, [])
        ev, al = self.poll_round(120, al)
        self.assertEqual(ev, [("ram_ok", "sal", 10_000)])
        self.assertEqual(al, set())

    def test_text(self):
        self.assertIn("RAM", tvserver.format_alert(("low_ram", "sal", 0)))
        self.assertIn("sal", tvserver.format_alert(("ram_ok", "sal", 0)))
