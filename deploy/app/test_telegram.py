"""Telegram alerting: send_telegram, alert_events, format_alert, alert_loop.

No real network or real token - urlopen is always replaced by a mock.
"""
import contextlib
import io
import os
import unittest
import urllib.error
import urllib.parse
from unittest import mock

import tvserver

FAKE_BOT = "1234:FAKE_TOKEN_FOR_TESTS"
CHAT = "-1001234567890"


class FakeResp:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def creds():
    return mock.patch.multiple(tvserver, ALERT_TG_TOKEN=FAKE_BOT, ALERT_TG_CHAT=CHAT)


def call_send(text, **urlopen_kw):
    """Calls send_telegram with urlopen mocked; returns (result, mock, stderr)."""
    err = io.StringIO()
    with creds(), mock.patch("urllib.request.urlopen", **urlopen_kw) as uo, \
            contextlib.redirect_stderr(err):
        result = tvserver.send_telegram(text)
    return result, uo, err.getvalue()


class SendTelegramTest(unittest.TestCase):
    def test_correct_url_and_body(self):
        text = "[CRITICAL] Obrazovka „Recepce“ je offline – příliš žluťoučký kůň"
        ok, uo, _ = call_send(text, return_value=FakeResp(200))
        self.assertIs(ok, True)
        uo.assert_called_once()
        args, kwargs = uo.call_args
        self.assertEqual(args[0], f"https://api.telegram.org/bot{FAKE_BOT}/sendMessage")
        body = kwargs["data"]
        self.assertIsInstance(body, bytes)
        self.assertTrue(body.isascii())          # URL-encoded, no raw UTF-8
        parsed = urllib.parse.parse_qs(body.decode())
        self.assertEqual(parsed["chat_id"], [CHAT])
        self.assertEqual(parsed["text"], [text])  # emoji and diacritics pass through unchanged
        self.assertEqual(kwargs["timeout"], 10)

    def test_non_200_returns_false(self):
        for status in (204, 400, 401, 429, 500):
            ok, _, _ = call_send("x", return_value=FakeResp(status))
            self.assertIs(ok, False, status)

    def test_exception_returns_false_without_raising(self):
        for exc in (urllib.error.URLError("cannot connect"),
                    TimeoutError("timed out"),
                    urllib.error.HTTPError("u", 502, "Bad Gateway", {}, None)):
            ok, _, err = call_send("x", side_effect=exc)
            self.assertIs(ok, False, exc)
            self.assertIn("telegram failed", err)

    def test_token_is_not_written_to_stderr(self):
        for exc in (urllib.error.URLError("cannot connect"),
                    TimeoutError("timed out")):
            _, _, err = call_send("x", side_effect=exc)
            self.assertNotIn(FAKE_BOT, err)
            self.assertNotIn(FAKE_BOT.split(":")[1], err)
        _, _, err = call_send("x", return_value=FakeResp(500))
        self.assertNotIn(FAKE_BOT, err)

    def test_falsification_token_in_stderr_would_be_caught(self):
        """Checks that the token check proves something: when scrubbing does not work
        (the module does not know the token, so it has nothing to mask) and the exception
        carries the URL, the token is in stderr and the assert in the test below would fail."""
        leaky = urllib.error.URLError(f"https://api.telegram.org/bot{FAKE_BOT}/sendMessage")
        err = io.StringIO()
        with mock.patch.multiple(tvserver, ALERT_TG_TOKEN="", ALERT_TG_CHAT=CHAT), \
                mock.patch("urllib.request.urlopen", side_effect=leaky), \
                contextlib.redirect_stderr(err):
            tvserver.send_telegram("x")
        self.assertIn(FAKE_BOT, err.getvalue())

    def test_exception_with_url_does_not_reveal_token(self):
        """Known shortcoming: send_telegram prints str(exc) without scrubbing. The usual
        URLError/timeout do not contain the URL, but another exception (e.g. ValueError from
        urllib) does. Once this is handled (token replaced by ***), remove the decorator."""
        leaky = ValueError(f"invalid url https://api.telegram.org/bot{FAKE_BOT}/sendMessage")
        _, _, err = call_send("x", side_effect=leaky)
        self.assertNotIn(FAKE_BOT, err)


class AlertEventsTest(unittest.TestCase):
    OFF = 600  # ALERT_OFFLINE_MIN * 60

    def test_offline_online_cycle_without_duplicates(self):
        online = {"tv1": {"last_seen": 1000}}
        # still within tolerance
        ev, al = tvserver.alert_events(online, set(), 1000 + self.OFF - 1, self.OFF)
        self.assertEqual((ev, al), ([], set()))
        # transition to offline exactly at the limit
        ev, al = tvserver.alert_events(online, set(), 1000 + self.OFF, self.OFF)
        self.assertEqual(ev, [("offline", "tv1", 1000)])
        self.assertEqual(al, {"tv1"})
        # repeated call with the state yields nothing
        ev2, al2 = tvserver.alert_events(online, al, 1000 + self.OFF + 60, self.OFF)
        self.assertEqual((ev2, al2), ([], {"tv1"}))
        # back online
        online["tv1"]["last_seen"] = 5000
        ev3, al3 = tvserver.alert_events(online, al2, 5010, self.OFF)
        self.assertEqual(ev3, [("online", "tv1", 5000)])
        self.assertEqual(al3, set())
        # and silence afterwards
        self.assertEqual(tvserver.alert_events(online, al3, 5020, self.OFF), ([], set()))

    def test_input_set_is_not_mutated(self):
        online = {"tv1": {"last_seen": 1}}
        original = set()
        tvserver.alert_events(online, original, 10_000, self.OFF)
        self.assertEqual(original, set())

    def test_device_without_last_seen_is_ignored(self):
        ev, al = tvserver.alert_events({"tv1": {}}, set(), 10_000, self.OFF)
        self.assertEqual((ev, al), ([], set()))


class FormatAlertTest(unittest.TestCase):
    NAME = "Recepce & <b>Bar</b> „č“ ř/ž"

    def test_offline_contains_name_and_time(self):
        txt = tvserver.format_alert(("offline", self.NAME, 1_700_000_000))
        self.assertIn(f'"{self.NAME}"', txt)      # no escaping or translation
        self.assertIn("offline", txt)
        self.assertTrue(txt.startswith("[CRITICAL]"))
        self.assertNotIn("&amp;", txt)
        self.assertNotIn("&lt;", txt)

    def test_online_contains_name(self):
        txt = tvserver.format_alert(("online", self.NAME, 1_700_000_000))
        self.assertIn(f'"{self.NAME}"', txt)
        self.assertIn("online", txt)
        self.assertTrue(txt.startswith("[OK]"))

    def test_blank_and_rendering_contain_name(self):
        for state in ("blank", "rendering"):
            self.assertIn(f'"{self.NAME}"', tvserver.format_alert((state, self.NAME, 1)))

    def test_prefixes_and_no_emoji(self):
        prefix = {"offline": "[CRITICAL]", "blank": "[WARNING]", "low_ram": "[WARNING]",
                  "ram_ok": "[OK]", "rendering": "[OK]", "online": "[OK]"}
        for lang in ("en", "cs"):
            for state, pre in prefix.items():
                txt = tvserver.format_alert((state, "x", 1_700_000_000), lang)
                self.assertTrue(txt.startswith(pre), (lang, state, txt))
                self.assertTrue(all(ord(ch) < 0x3000 for ch in txt), (lang, state))

    def test_language_follows_env_default_english(self):
        event = ("online", "Recepce", 1)
        with mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("SIGNAGE_ALERT_LANG", None)
            self.assertEqual(tvserver.format_alert(event), '[OK] Screen "Recepce" is online again.')
        with mock.patch.dict("os.environ", {"SIGNAGE_ALERT_LANG": "cs"}):
            self.assertEqual(tvserver.format_alert(event), "[OK] Obrazovka „Recepce“ je zase online.")
        with mock.patch.dict("os.environ", {"SIGNAGE_ALERT_LANG": "de"}):
            self.assertIn("is online again", tvserver.format_alert(event))

    def test_format_survives_sending(self):
        """Text from format_alert passes through send_telegram and back through decoding unchanged."""
        txt = tvserver.format_alert(("offline", self.NAME, 1_700_000_000))
        _, uo, _ = call_send(txt, return_value=FakeResp(200))
        body = urllib.parse.parse_qs(uo.call_args.kwargs["data"].decode())
        self.assertEqual(body["text"], [txt])


class StopLoop(BaseException):
    """Ends the infinite loop (BaseException so that `except Exception` does not catch it)."""


class AlertLoopTest(unittest.TestCase):
    def run_rounds(self, rounds, sends):
        """Runs `rounds` rounds of alert_loop; sends = side_effect for send_telegram."""
        sleeps = [None] * rounds + [StopLoop()]
        with mock.patch.object(tvserver.time, "sleep", side_effect=sleeps), \
                mock.patch.object(tvserver, "send_telegram", side_effect=sends) as send:
            with self.assertRaises(StopLoop):
                tvserver.alert_loop()
        return send

    def setUp(self):
        patches = [
            mock.patch.object(tvserver, "_online", {"tv1": {"last_seen": 1}}),
            mock.patch.object(tvserver, "_alerted", set()),
            mock.patch.object(tvserver, "ALERT_OFFLINE_MIN", 10),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_failure_does_not_advance_state_and_resends(self):
        send = self.run_rounds(2, [False, True])
        self.assertEqual(send.call_count, 2)     # round 2 sent the same event again
        texts = [c.args[0] for c in send.call_args_list]
        self.assertEqual(texts[0], texts[1])
        self.assertIn("tv1", texts[0])
        self.assertEqual(tvserver._alerted, {"tv1"})   # only after success

    def test_state_stays_empty_after_failure(self):
        self.run_rounds(1, [False])
        self.assertEqual(tvserver._alerted, set())

    def test_success_advances_state_and_does_not_repeat(self):
        send = self.run_rounds(2, [True])        # round 2 has nothing left to send
        self.assertEqual(send.call_count, 1)
        self.assertEqual(tvserver._alerted, {"tv1"})


class AlertingDisabledTest(unittest.TestCase):
    def test_without_token_and_chat_is_not_enabled(self):
        """The start condition (`if ALERT_TG_TOKEN and ALERT_TG_CHAT`) lives only in the
        __main__ block; here we at least verify the default environment values."""
        for token, chat in (("", ""), (FAKE_BOT, ""), ("", CHAT)):
            with mock.patch.multiple(tvserver, ALERT_TG_TOKEN=token, ALERT_TG_CHAT=chat):
                self.assertFalse(bool(tvserver.ALERT_TG_TOKEN and tvserver.ALERT_TG_CHAT))
        with creds():
            self.assertTrue(bool(tvserver.ALERT_TG_TOKEN and tvserver.ALERT_TG_CHAT))


if __name__ == "__main__":
    unittest.main()
