"""Playlist item schedule: validation, storing, deleting, playlist, cs/en errors."""
import json
import os

import tvserver
from test_http import HttpBase

GOOD = {"days": [0, 2, 4], "from": "08:00", "to": "17:30"}


class ScheduleTest(HttpBase):
    def setUp(self):
        super().setUp()
        with open(os.path.join(tvserver.MEDIA, "a.png"), "wb") as fh:
            fh.write(b"x")
        tvserver._state["devices"]["d1"] = {"version": 1, "items": [
            {"file": "a.png", "type": "image", "seconds": 10}]}

    def item(self, **kw):
        body = {"device": "d1", "file": "a.png"}
        body.update(kw)
        st, hdr, raw = self.req("POST", "/api/item", body)
        return st, hdr, json.loads(raw)

    def stored(self):
        return tvserver._state["devices"]["d1"]["items"][0]

    def playlist_item(self, path="/api/tv/d1/playlist"):
        st, _, raw = self.req("GET", path, auth=False)
        self.assertEqual(st, 200)
        return json.loads(raw)["items"][0]

    def test_stores_valid_schedule_and_playlist_returns_it(self):
        st, _, _ = self.item(schedule=GOOD)
        self.assertEqual(st, 200)
        self.assertEqual(self.stored()["schedule"], GOOD)
        self.assertEqual(self.playlist_item()["schedule"], GOOD)

    def test_without_schedule_playlist_has_no_key(self):
        self.assertNotIn("schedule", self.playlist_item())
        self.item(seconds=5)
        self.assertNotIn("schedule", self.playlist_item())

    def test_schedule_with_only_time_or_only_days(self):
        self.assertEqual(self.item(schedule={"from": "22:00", "to": "02:00"})[0], 200)
        self.assertEqual(self.stored()["schedule"], {"from": "22:00", "to": "02:00"})
        self.assertEqual(self.item(schedule={"days": [6, 5]})[0], 200)
        self.assertEqual(self.stored()["schedule"], {"days": [5, 6]})

    def test_null_and_empty_object_clear_schedule(self):
        for empty in (None, {}, {"days": []}):
            with self.subTest(empty=empty):
                self.item(schedule=GOOD)
                st, _, _ = self.item(schedule=empty)
                self.assertEqual(st, 200)
                self.assertNotIn("schedule", self.stored())
                self.assertNotIn("schedule", self.playlist_item())

    def test_changing_other_field_keeps_schedule(self):
        self.item(schedule=GOOD)
        self.item(seconds=7)
        self.assertEqual(self.stored()["schedule"], GOOD)

    def test_invalid_schedules_get_400_schedule_invalid(self):
        bad = [
            "x", 5, [], True,
            {"days": "0"}, {"days": [7]}, {"days": [-1]}, {"days": [1, 1]},
            {"days": [True]}, {"days": [1.5]}, {"days": ["1"]},
            {"from": "08:00"}, {"to": "08:00"},
            {"from": "8:00", "to": "09:00"}, {"from": "24:00", "to": "09:00"},
            {"from": "08:60", "to": "09:00"}, {"from": "08:00", "to": "0900"},
            {"from": 800, "to": "09:00"}, {"from": "08:00", "to": None},
            {"from": "08:00", "to": "08:00"},
            {"days": [0], "foo": 1},
        ]
        self.item(schedule=GOOD)
        for b in bad:
            with self.subTest(schedule=b):
                st, _, d = self.item(schedule=b)
                self.assertEqual(st, 400)
                self.assertEqual(d["code"], "schedule_invalid")
        self.assertEqual(self.stored()["schedule"], GOOD)   # nothing changed

    def test_time_boundaries_pass(self):
        self.assertEqual(self.item(schedule={"from": "00:00", "to": "23:59"})[0], 200)
        self.assertEqual(self.item(schedule={"from": "23:59", "to": "00:00"})[0], 200)

    def test_error_cs_en_and_default(self):
        self.assertIn("schedule_invalid", tvserver.ERRORS)
        self.assertTrue(tvserver.ERRORS["schedule_invalid"]["cs"])
        self.assertTrue(tvserver.ERRORS["schedule_invalid"]["en"])
        for lang in ("cs", "en"):
            st, hdr, raw = self.req("POST", f"/api/item?lang={lang}",
                                    {"device": "d1", "file": "a.png", "schedule": "x"})
            d = json.loads(raw)
            self.assertEqual(st, 400)
            self.assertEqual(d["error"], tvserver.ERRORS["schedule_invalid"][lang])
        self.assertNotEqual(tvserver.ERRORS["schedule_invalid"]["cs"],
                            tvserver.ERRORS["schedule_invalid"]["en"])

    def test_schedule_survives_save_and_load(self):
        self.item(schedule=GOOD)
        with open(tvserver.STATE) as fh:
            self.assertEqual(json.load(fh)["devices"]["d1"]["items"][0]["schedule"], GOOD)

    def test_corrupt_schedule_in_state_is_dropped_on_load(self):
        self.assertIsNone(tvserver.clean_schedule({"days": [9]}))
        self.assertIsNone(tvserver.clean_schedule("x"))
        self.assertEqual(tvserver.clean_schedule(GOOD), GOOD)
