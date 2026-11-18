import json
import os
import tempfile
import unittest

from tvserver import (_env_int, _container_ok, _online, _repack_reason,
                      alert_events, auto_repack, format_alert, is_blank,
                      kind, media_path, media_warnings, playlist_settings, qr_caption,
                      qr_cards, qr_target, safe, unsupported_msg,
                      _render_report, touch_device,
                      validate_media,
                      _range_bounds,
                      DEFAULT_QR_URL)
import video_convert
import tvserver


# --- helpers for building minimal test files --------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 32
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 32


def box(fourcc, payload=b""):
    """MP4/MOV box: 4B size (including header) + 4B type + payload."""
    return (len(payload) + 8).to_bytes(4, "big") + fourcc + payload


def tkhd(w, h, version=0):
    """tkhd box with track dimensions (16.16 fixed), both v0 and v1 (64-bit times)."""
    times = b"\x00" * (32 if version == 1 else 20)   # create/modify/id/reserved/duration
    body = bytes([version]) + b"\x00\x00\x00" + times
    body += b"\x00" * 16                             # reserved, layer, group, volume
    body += b"\x00" * 36                             # transformation matrix
    body += int(w * 65536).to_bytes(4, "big") + int(h * 65536).to_bytes(4, "big")
    return box(b"tkhd", body)


def visual_entry(codec, w, h):
    """Full VisualSampleEntry (86 B) - dimensions sit at bytes 32 and 34 of the box."""
    body = b"\x00" * 8                    # reserved + data_reference_index
    body += b"\x00" * 16                  # pre_defined + reserved
    body += w.to_bytes(2, "big") + h.to_bytes(2, "big")
    body += b"\x00\x48\x00\x00" * 2      # 72 dpi resolution, horizontal and vertical
    body += b"\x00" * 4 + b"\x00\x01"     # reserved + frame_count
    body += b"\x00" * 32                  # compressorname
    body += b"\x00\x18\xff\xff"           # depth + pre_defined (-1)
    return box(codec, body)


def mp4(codec, w=None, h=None, version=0, entry=None):
    """Minimal MP4 with one video sample entry, optionally with tkhd dimensions."""
    entry = entry if entry is not None else box(codec, b"\x00" * 70)
    stsd = box(b"stsd", b"\x00\x00\x00\x00" + (1).to_bytes(4, "big") + entry)
    track = tkhd(w, h, version) if w is not None else b""
    trak = box(b"trak", track + box(b"mdia", box(b"minf", box(b"stbl", stsd))))
    return box(b"ftyp", b"isom\x00\x00\x02\x00isomavc1") + box(b"moov", trak)


def mp4_moov_at_end(mb=6, w=1280, h=720):
    """MP4 where mdat precedes moov - exactly what a missing faststart does."""
    head = box(b"ftyp", b"isom\x00\x00\x02\x00isomavc1")
    full = mp4(b"avc1", w, h)
    return head + box(b"mdat", b"\x00" * (mb * 1024 * 1024)) + full[len(head):]


def mp4_faststart(mb=6, w=1280, h=720):
    """MP4 with the correct order: ftyp, moov, only then mdat."""
    return mp4(b"avc1", w, h) + box(b"mdat", b"\x00" * (mb * 1024 * 1024))


def png(w, h):
    """PNG with a real IHDR (width and height, 4 bytes each, big-endian)."""
    ihdr = (13).to_bytes(4, "big") + b"IHDR" + w.to_bytes(4, "big") + h.to_bytes(4, "big")
    return b"\x89PNG\r\n\x1a\x0a" + ihdr + b"\x08\x02\x00\x00\x00" + b"\x00" * 8


def jpeg(w, h, marker=b"\xc0"):
    """JPEG with APP0 and an SOFn marker; SOF holds height first, then width."""
    app0 = b"\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\x00" + b"\x00" * 9
    sof = (b"\xff" + marker + (17).to_bytes(2, "big") + b"\x08"
           + h.to_bytes(2, "big") + w.to_bytes(2, "big") + b"\x03" + b"\x00" * 9)
    return b"\xff\xd8" + app0 + sof + b"\xff\xda\x00\x08" + b"\x00" * 8


def gif(w, h):
    """GIF89a with a screen descriptor (dimensions little-endian)."""
    return (b"GIF89a" + w.to_bytes(2, "little") + h.to_bytes(2, "little")
            + b"\x00" * 4)


def riff(chunk):
    """Wrap a WebP chunk in a RIFF/WEBP header."""
    return b"RIFF" + (len(chunk) + 4).to_bytes(4, "little") + b"WEBP" + chunk


def webp_lossy(w, h):
    """Lossy WebP VP8: sync code 9d 01 2a followed by 14-bit dimensions."""
    body = (b"\x00\x00\x00\x9d\x01\x2a" + w.to_bytes(2, "little")
            + h.to_bytes(2, "little") + b"\x00" * 4)
    return riff(b"VP8 " + len(body).to_bytes(4, "little") + body)


def webp_lossless(w, h):
    """WebP VP8L: signature 0x2f, then (width-1) and (height-1), 14 bits each."""
    body = b"\x2f" + (((w - 1) | ((h - 1) << 14))).to_bytes(4, "little") + b"\x00" * 4
    return riff(b"VP8L" + len(body).to_bytes(4, "little") + body)


def webp_extended(w, h):
    """WebP VP8X: canvas dimensions, 3 bytes little-endian each, again minus one."""
    body = b"\x00" * 4 + (w - 1).to_bytes(3, "little") + (h - 1).to_bytes(3, "little")
    return riff(b"VP8X" + len(body).to_bytes(4, "little") + body)


def qtmov(codec, compatible=b"qt  "):
    """MOV with the QuickTime brand 'qt  ' - exactly what ended up on the panel."""
    entry = box(codec, b"\x00" * 70)
    stsd = box(b"stsd", b"\x00\x00\x00\x00" + (1).to_bytes(4, "big") + entry)
    moov = box(b"moov", box(b"trak", box(b"mdia", box(b"minf", box(b"stbl", stsd)))))
    return box(b"ftyp", b"qt  \x00\x00\x02\x00" + compatible) + moov


class ValidateMediaTest(unittest.TestCase):
    def test_accepts_valid_images(self):
        for name, data in (("a.png", PNG), ("b.jpg", JPEG),
                           ("c.gif", GIF), ("d.webp", WEBP)):
            self.assertIsNone(validate_media(name, data), name)

    def test_rejects_image_with_foreign_content(self):
        err = validate_media("foto.jpg", PNG)
        self.assertIsNotNone(err)
        self.assertIn("jpg", err)

    def test_rejects_junk_image(self):
        self.assertIsNotNone(validate_media("foto.png", b"tohle neni obrazek"))

    def test_accepts_mp4_with_h264(self):
        self.assertIsNone(validate_media("klip.mp4", mp4(b"avc1")))
        self.assertIsNone(validate_media("klip.m4v", mp4(b"avc3")))

    def test_accepts_mov_with_h264(self):
        self.assertIsNone(validate_media("klip.mov", mp4(b"avc1")))

    def test_rejects_quicktime_mov_and_advises_reencode(self):
        """A qt-brand MOV does not play in the TV browser - it must fail at upload."""
        err = validate_media("klip.mov", qtmov(b"avc1"))
        self.assertIsNotNone(err)
        self.assertIn("mp4", err.lower())

    def test_accepts_mov_compatible_with_mp4(self):
        """qt as major brand, but isom among compatible brands => playable."""
        self.assertIsNone(validate_media("klip.mov", qtmov(b"avc1", b"qt  isom")))

    def test_rejects_h265_mp4_and_advises_reencode(self):
        for codec in (b"hev1", b"hvc1", b"av01"):
            err = validate_media("klip.mp4", mp4(codec))
            self.assertIsNotNone(err, codec)
            self.assertIn("H.264", err)

    def test_rejects_mp4_without_container(self):
        self.assertIsNotNone(validate_media("klip.mp4", b"neni to video ani nahodou"))

    def test_accepts_mp4_with_unknown_codec(self):
        # better to let unknown codecs through than to block valid files
        self.assertIsNone(validate_media("klip.mp4", mp4(b"xxxx")))

    def test_webm_by_magic_header(self):
        self.assertIsNone(validate_media("klip.webm", WEBM))
        self.assertIsNotNone(validate_media("klip.webm", b"rozbite"))


class DimensionsTest(unittest.TestCase):
    """Dimensions are read straight from the container - no PIL, no ffmpeg.

    Policy: large video is NOT rejected. The service also drives modern large
    TVs where a 4K spot is a legitimate request; rejecting it would be a bug.
    It only warns - and when the screen itself reports its canvas size, the
    warning is based on that.
    """

    def tearDown(self):
        _online.clear()

    # --- reading dimensions from the container ---------------------------

    def test_reads_size_from_tkhd_version_0_and_1(self):
        for v in (0, 1):
            w = media_warnings("klip.mp4", mp4(b"avc1", 3840, 2160, version=v))
            self.assertTrue(any("3840x2160" in t for t in w), "verze %d" % v)

    def test_reads_size_from_stsd_when_tkhd_missing(self):
        data = mp4(b"avc1", entry=visual_entry(b"avc1", 3840, 2160))
        self.assertTrue(any("3840x2160" in t for t in media_warnings("k.mp4", data)))

    def test_anamorphic_video_judged_by_coded_resolution(self):
        # tkhd carries the size after stretching (non-square pixels), stsd the
        # really decoded resolution - what the decoder actually handles decides
        data = mp4(b"avc1", 3840, 1080, entry=visual_entry(b"avc1", 1920, 1080))
        self.assertEqual(media_warnings("klip.mp4", data), [])

    def test_unreadable_size_warns_nothing(self):
        self.assertEqual(media_warnings("klip.mp4", mp4(b"avc1")), [])
        self.assertEqual(media_warnings("klip.mp4", mp4(b"avc1", 0, 0)), [])

    # --- policy: 4K passes, only a warning is shown -----------------------

    def test_4k_video_passes_validation(self):
        """Key point: a customer may want a 4K spot on a large TV on purpose."""
        self.assertIsNone(validate_media("klip.mp4", mp4(b"avc1", 3840, 2160)))

    def test_4k_video_warns_about_older_panels(self):
        w = media_warnings("klip.mp4", mp4(b"avc1", 3840, 2160))
        self.assertEqual(len(w), 1)
        self.assertIn("3840x2160", w[0])
        self.assertIn("1920x1080", w[0])

    def test_fullhd_landscape_and_portrait_no_warning(self):
        self.assertEqual(media_warnings("k.mp4", mp4(b"avc1", 1920, 1080)), [])
        self.assertEqual(media_warnings("k.mp4", mp4(b"avc1", 1080, 1920)), [])

    def test_video_one_pixel_longer_warns(self):
        self.assertTrue(media_warnings("k.mp4", mp4(b"avc1", 1921, 1080)))

    def test_warns_even_if_only_shorter_side_exceeds_limit(self):
        w = media_warnings("k.mp4", mp4(b"avc1", 1280, 1200))
        self.assertTrue(any("1280x1200" in t for t in w))

    # --- warnings based on the real screen --------------------------------

    def test_4k_video_on_4k_screen_no_warning(self):
        _online["sal"] = {"screen": {"w": 3840, "h": 2160}}
        self.assertEqual(media_warnings("k.mp4", mp4(b"avc1", 3840, 2160), "sal"), [])

    def test_small_screen_warns_and_states_its_size(self):
        _online["panel"] = {"screen": {"w": 1280, "h": 720}}
        w = media_warnings("k.mp4", mp4(b"avc1", 1920, 1080), "panel")
        self.assertEqual(len(w), 1)
        self.assertIn("1280x720", w[0])
        self.assertIn("panel", w[0])

    def test_reported_size_overrides_default_estimate(self):
        # without a report 4K would warn; the screen said it can handle it
        _online["sal"] = {"screen": {"w": 3840, "h": 2160}}
        self.assertTrue(media_warnings("k.mp4", mp4(b"avc1", 3840, 2160)))
        self.assertEqual(media_warnings("k.mp4", mp4(b"avc1", 3840, 2160), "sal"), [])

    # --- faststart ---------------------------------------------------------

    def test_moov_at_end_of_large_file_warns(self):
        w = media_warnings("klip.mp4", mp4_moov_at_end(6))
        self.assertTrue(any("faststart" in t for t in w))

    def test_small_file_without_faststart_ignored(self):
        self.assertEqual(media_warnings("klip.mp4", mp4_moov_at_end(0)), [])

    def test_correct_box_order_no_warning(self):
        w = media_warnings("klip.mp4", mp4_faststart(6))
        self.assertFalse(any("faststart" in t for t in w))

    # --- images -------------------------------------------------------------

    def test_large_photo_passes_but_warns(self):
        data = jpeg(8000, 6000)
        self.assertIsNone(validate_media("foto.jpg", data))
        w = media_warnings("foto.jpg", data)
        self.assertTrue(any("48.0 Mpx" in t for t in w))

    def test_absurd_photo_rejected_as_memory_safeguard(self):
        err = validate_media("foto.png", png(12000, 12000))     # 144 Mpx
        self.assertIsNotNone(err)
        self.assertIn("100 Mpx", err)

    def test_too_long_side_rejected(self):
        err = validate_media("panorama.png", png(25000, 800))
        self.assertIsNotNone(err)
        self.assertIn("20000", err)

    def test_ordinary_photo_no_warning(self):
        self.assertEqual(media_warnings("foto.jpg", jpeg(4000, 3000)), [])
        self.assertEqual(media_warnings("anim.gif", gif(1920, 1080)), [])

    def test_25_mpx_warning_boundary(self):
        self.assertEqual(media_warnings("a.png", png(5000, 5000)), [])    # 25.0 Mpx
        self.assertTrue(media_warnings("b.png", png(5000, 5001)))         # one row more


class UnsupportedFormatTest(unittest.TestCase):
    """A generic message does not help the client - for common formats we give concrete advice."""

    def test_iphone_heic_advises_how_to_fix(self):
        msg = unsupported_msg("IMG_1234.HEIC")
        self.assertIn("iPhone", msg)
        self.assertIn("Compatible", msg)

    def test_common_video_formats_point_to_conversion(self):
        for name in ("film.mkv", "klip.avi", "z-kamery.mts", "stary.wmv"):
            msg = unsupported_msg(name)
            self.assertIn("mp4", msg, name)
            self.assertIn("convert-video.sh", msg, name)

    def test_unknown_extension_keeps_generic_message(self):
        msg = unsupported_msg("neco.xyz")
        self.assertIn("unsupported type", msg)
        self.assertIn(".jpg", msg)

    def test_messages_are_ascii(self):
        for name in ("a.heic", "b.mkv", "c.xyz"):
            self.assertTrue(unsupported_msg(name).isascii(), name)


class AlertBlankScreenTest(unittest.TestCase):
    """A blank screen is a different fault from offline - and it must alert too."""

    def _online(self, **kw):
        base = {"last_seen": 1000, "err": 0, "last_ok": 1000}
        base.update(kw)
        return {"tv": base}

    def test_healthy_screen_does_not_alert(self):
        ev, al = alert_events(self._online(), set(), 1000, 600)
        self.assertEqual(ev, [])
        self.assertEqual(al, set())

    def test_blank_screen_alerts(self):
        ev, al = alert_events(self._online(err=2, last_ok=0), set(), 1000, 600)
        self.assertEqual([e[0] for e in ev], ["blank"])
        self.assertIn("shows nothing", format_alert(ev[0]))
        self.assertIn("nic na ní není", format_alert(ev[0], "cs"))

    def test_blank_screen_alerts_only_once(self):
        ev, al = alert_events(self._online(err=2, last_ok=0), set(), 1000, 600)
        ev2, _ = alert_events(self._online(err=2, last_ok=0), al, 1001, 600)
        self.assertEqual(ev2, [])

    def test_return_to_rendering_is_announced(self):
        _, al = alert_events(self._online(err=2, last_ok=0), set(), 1000, 600)
        ev, al2 = alert_events(self._online(err=0, last_ok=1000), al, 1000, 600)
        self.assertEqual([e[0] for e in ev], ["rendering"])
        self.assertEqual(al2, set())

    def test_offline_screen_does_not_also_report_blank(self):
        # otherwise a switched-off TV would produce two messages about the same thing
        ev, _ = alert_events(self._online(last_seen=100, err=5, last_ok=0),
                             set(), 1000, 600)
        self.assertEqual([e[0] for e in ev], ["offline"])


class BlankScreenTest(unittest.TestCase):
    """is_blank: the screen reports in, but nothing has been on it for a long time."""

    def test_healthy_screen_is_not_blank(self):
        self.assertFalse(is_blank({"last_seen": 1000, "err": 0, "last_ok": 990}, 1000))

    def test_offline_screen_is_not_blank(self):
        # silent for a long time - another alert handles that, here it would only be noise
        self.assertFalse(is_blank({"last_seen": 500, "err": 3, "last_ok": 0}, 1000))

    def test_reports_errors_and_never_rendered(self):
        self.assertTrue(is_blank({"last_seen": 1000, "err": 3, "last_ok": 0}, 1000))

    def test_errors_but_rendered_recently(self):
        self.assertFalse(is_blank({"last_seen": 1000, "err": 1, "last_ok": 950}, 1000))

    def test_errors_and_nothing_for_long(self):
        self.assertTrue(is_blank({"last_seen": 1000, "err": 1, "last_ok": 700}, 1000))

    def test_empty_record(self):
        self.assertFalse(is_blank({}, 1000))
        self.assertFalse(is_blank(None, 1000))



class AlertEventsTest(unittest.TestCase):
    NOW = 1_000_000
    LIMIT = 600  # 10 minutes

    def seen(self, before):
        return {"last_seen": self.NOW - before}

    def test_freshly_seen_device_reports_nothing(self):
        events, alerted = alert_events({"tv1": self.seen(30)}, set(),
                                       self.NOW, self.LIMIT)
        self.assertEqual(events, [])
        self.assertEqual(alerted, set())

    def test_over_limit_reports_offline(self):
        events, alerted = alert_events({"tv1": self.seen(700)}, set(),
                                       self.NOW, self.LIMIT)
        self.assertEqual(events, [("offline", "tv1", self.NOW - 700)])
        self.assertEqual(alerted, {"tv1"})

    def test_offline_not_reported_repeatedly(self):
        events, alerted = alert_events({"tv1": self.seen(700)}, {"tv1"},
                                       self.NOW, self.LIMIT)
        self.assertEqual(events, [])
        self.assertEqual(alerted, {"tv1"})

    def test_back_online_reports_recovery(self):
        events, alerted = alert_events({"tv1": self.seen(30)}, {"tv1"},
                                       self.NOW, self.LIMIT)
        self.assertEqual(events, [("online", "tv1", self.NOW - 30)])
        self.assertEqual(alerted, set())

    def test_never_seen_device_is_ignored(self):
        events, alerted = alert_events({"tv1": {"last_seen": 0}}, set(),
                                       self.NOW, self.LIMIT)
        self.assertEqual(events, [])
        self.assertEqual(alerted, set())

    def test_message_contains_device_and_state(self):
        offline = format_alert(("offline", "studio1", self.NOW - 700))
        online = format_alert(("online", "studio1", self.NOW - 30))
        self.assertIn("studio1", offline)
        self.assertIn("offline", offline)
        self.assertIn("studio1", online)
        self.assertIn("online", online)


class QrTargetTest(unittest.TestCase):
    def test_empty_target_is_default_instagram(self):
        self.assertEqual(qr_target(""), DEFAULT_QR_URL)

    def test_bare_email_normalized_to_mailto(self):
        self.assertEqual(qr_target("hello@example.com"),
                         "mailto:hello@example.com")

    def test_mailto_and_web_unchanged(self):
        self.assertEqual(qr_target("mailto:a@example.com"), "mailto:a@example.com")
        self.assertEqual(qr_target("https://demo-brand.example/akce"),
                         "https://demo-brand.example/akce")


class QrCaptionTest(unittest.TestCase):
    def test_instagram_shows_handle(self):
        kind_, label, handle = qr_caption("https://www.instagram.com/demo_brand/")
        self.assertEqual(kind_, "instagram")
        self.assertIn("Instagram", label)
        self.assertEqual(handle, "@demo_brand")

    def test_email_shows_full_address(self):
        for url in ("hello@example.com", "mailto:hello@example.com"):
            kind_, label, handle = qr_caption(url)
            self.assertEqual(kind_, "email", url)
            self.assertEqual(handle, "hello@example.com", url)
            self.assertNotIn("Instagram", label)

    def test_web_shows_domain_without_www(self):
        kind_, label, handle = qr_caption("https://www.demo-brand.example/akce")
        self.assertEqual(kind_, "web")
        self.assertEqual(handle, "demo-brand.example/akce")

    def test_default_labels_are_english(self):
        # the player maps these defaults to its own language (en/cs)
        labels = {qr_caption(u)[1] for u in ("https://www.instagram.com/x/", "a@example.com",
                                             "https://www.web.cz/a", "neco divneho")}
        self.assertEqual(labels, {"Follow us on Instagram", "Write to us",
                                  "Find more here", "Scan to take a look"})

    def test_empty_target_behaves_as_default_instagram(self):
        self.assertEqual(qr_caption(""), qr_caption(DEFAULT_QR_URL))

    def test_nonsense_text_shown_as_is(self):
        kind_, label, handle = qr_caption("neco divneho")
        self.assertEqual(handle, "neco divneho")


class QrCardsTest(unittest.TestCase):
    def test_default_is_two_cards_ig_and_email(self):
        cards = qr_cards({"brand": {"url": "https://www.instagram.com/demo_brand/"}})
        self.assertEqual([c["kind"] for c in cards], ["instagram"])
        cards = qr_cards({"brand": {"url": "https://www.instagram.com/demo_brand/",
                                    "qrEmail": "hello@example.org"}})
        self.assertEqual([c["kind"] for c in cards], ["instagram", "email"])
        # the logo already says the name - nothing is repeated under the IG QR
        self.assertEqual(cards[0]["handle"], "")
        self.assertEqual(cards[1]["handle"], "hello@example.org")

    def test_first_qr_can_be_disabled(self):
        cards = qr_cards({"qr1On": False, "brand": {"qrEmail": "hello@example.org"}})
        self.assertEqual([c["kind"] for c in cards], ["email"])

    def test_second_qr_can_be_disabled(self):
        cards = qr_cards({"qr2On": False, "brand": {"url": "https://www.instagram.com/demo_brand/"}})
        self.assertEqual([c["kind"] for c in cards], ["instagram"])

    def test_placeholder_targets_never_become_cards(self):
        self.assertEqual(qr_cards({"qrUrl": "https://example.com/", "qrUrl2": "info@example.com"}), [])

    def test_both_disabled_means_no_qr(self):
        self.assertEqual(qr_cards({"qr1On": False, "qr2On": False}), [])

    def test_same_target_in_both_slots_merges(self):
        cards = qr_cards({"qrUrl": "hello@example.org", "brand": {"qrEmail": "hello@example.org"}})
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["kind"], "email")

    def test_custom_second_target_overrides_default_email(self):
        cards = qr_cards({"qrUrl": "https://www.instagram.com/demo_brand/",
                          "qrUrl2": "https://demo-brand.example"})
        self.assertEqual(cards[1]["kind"], "web")
        self.assertEqual(cards[1]["handle"], "demo-brand.example")


class PlaylistSettingsTest(unittest.TestCase):
    def test_default_settings(self):
        s = playlist_settings({"qrUrl": "https://www.instagram.com/demo_brand/",
                               "brand": {"qrEmail": "hello@example.org"}})
        self.assertFalse(s["qrCorner"])
        self.assertTrue(s["qr1On"])
        self.assertTrue(s["qr2On"])
        self.assertEqual(len(s["qrCards"]), 2)
        self.assertEqual(s["qrHandle"], "@demo_brand")
        self.assertEqual(s["qrUrl"], "https://www.instagram.com/demo_brand/")
        self.assertEqual(playlist_settings({})["qrUrl"], DEFAULT_QR_URL)
        self.assertEqual(s["transition"], "fade")

    def test_disabled_qr_disappears_from_cards(self):
        s = playlist_settings({"qr1On": False, "qr2On": False})
        self.assertEqual(s["qrCards"], [])

    def test_labels_match_target(self):
        s = playlist_settings({"qrUrl": "hello@example.com"})
        self.assertEqual(s["qrKind"], "email")
        self.assertEqual(s["qrHandle"], "hello@example.com")


class DisplayModeTest(unittest.TestCase):
    """Content orientation per screen. The default must be today's behaviour."""

    def test_default_is_landscape(self):
        self.assertEqual(playlist_settings({})["fit"], "landscape")

    def test_chosen_option_propagates_to_player(self):
        for choice in ("landscape", "portrait", "portrait270", "fill"):
            self.assertEqual(playlist_settings({"fit": choice})["fit"], choice)

    def test_unknown_value_behaves_as_before(self):
        # Neither a typo nor an old stored value may upset the screen.
        for bad in ("", None, "výška", "cover", 7, True, "PORTRAIT "):
            self.assertEqual(playlist_settings({"fit": bad})["fit"], "landscape")


class KioskMarkerTest(unittest.TestCase):
    """Screen in the kiosk vs. opened in a browser - the User-Agent cannot tell,
    so the app signs itself into the page and the player passes it on."""

    def test_empty_when_nothing_reported(self):
        self.assertEqual(_render_report({"w": ["1920"], "h": ["1080"]})["kiosk"], "")

    def test_app_version_is_read(self):
        self.assertEqual(_render_report({"kiosk": ["1.1"]})["kiosk"], "1.1")

    def test_forged_value_is_discarded(self):
        for bad in ("<script>", "verze s mezerou", "1.1;rm", ""):
            self.assertEqual(_render_report({"kiosk": [bad]})["kiosk"], "")

    def test_marker_survives_next_heartbeat_without_it(self):
        # The player sends kiosk only when it knows it; a once-reported value
        # must not be forgotten on the next heartbeat, or the badge in admin would flicker.
        _online.pop("kiosk-test", None)
        touch_device("kiosk-test", {}, _render_report({"kiosk": ["1.1"], "ok": ["1"]}))
        touch_device("kiosk-test", {}, _render_report({"ok": ["1"]}))
        self.assertEqual(_online["kiosk-test"]["kiosk"], "1.1")
        _online.pop("kiosk-test", None)


class CacheStateTest(unittest.TestCase):
    """How many playlist items are stored in the browser - otherwise there is no
    telling whether the screen survives a network outage."""

    def test_value_is_read(self):
        self.assertEqual(_render_report({"cache": ["2/2"]})["cache"], "2/2")

    def test_junk_is_discarded(self):
        for bad in ("2 z 2", "-1/2", "<b>", "", "abc/def"):
            self.assertEqual(_render_report({"cache": [bad]})["cache"], "")

    def test_survives_heartbeat_without_value(self):
        _online.pop("cache-test", None)
        touch_device("cache-test", {}, _render_report({"cache": ["2/2"], "ok": ["1"]}))
        touch_device("cache-test", {}, _render_report({"ok": ["1"]}))
        self.assertEqual(_online["cache-test"]["cache"], "2/2")
        _online.pop("cache-test", None)

    def test_incomplete_bundle_reported_as_is(self):
        # The difference between 1/2 and 2/2 is exactly what the client only notices during an outage.
        self.assertEqual(_render_report({"cache": ["1/2"]})["cache"], "1/2")


class SmoothnessTest(unittest.TestCase):
    """Dropped frames and stalls while waiting for data. The server cannot detect this on its own."""

    def test_dropped_frames_are_read(self):
        self.assertEqual(_render_report({"drop": ["12/1800"]})["drop"], "12/1800")

    def test_junk_is_discarded(self):
        for bad in ("12 z 1800", "-1/10", "", "x/y"):
            self.assertEqual(_render_report({"drop": [bad]})["drop"], "")

    def test_stalls_parsed_as_number(self):
        self.assertEqual(_render_report({"stall": ["3"]})["stall"], 3)
        self.assertEqual(_render_report({"stall": ["nesmysl"]})["stall"], 0)

    def test_values_survive_heartbeat_without_them(self):
        _online.pop("plynulost-test", None)
        touch_device("plynulost-test", {},
                     _render_report({"drop": ["5/900"], "stall": ["2"], "ok": ["1"]}))
        touch_device("plynulost-test", {}, _render_report({"ok": ["1"]}))
        self.assertEqual(_online["plynulost-test"]["drop"], "5/900")
        self.assertEqual(_online["plynulost-test"]["stall"], 2)
        _online.pop("plynulost-test", None)


class PlayerCssTest(unittest.TestCase):
    """Two rules in the player that the server does not test, but whose loss only
    shows on the client's screen - hence at least this check."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tv", "player.html")
        with open(path, encoding="utf-8") as f:
            cls.player = f.read()

    def test_sharp_content_above_blurred_background(self):
        # .item is a stacking context because of translateZ(0), so a .bg descendant
        # with z-index:-1 is painted ABOVE its own background. The sharp image
        # therefore needs its own layer, or the blurred copy covers it (it happened).
        self.assertIn(".item::after", self.player)
        self.assertRegex(self.player, r"\.item::after\s*\{[^}]*background-image:\s*inherit")
        self.assertRegex(self.player, r"\.item::after\s*\{[^}]*z-index:\s*1")

    def test_idle_screen_portrait_scales_from_vh(self):
        # After rotation the canvas width equals the viewport HEIGHT, so sizes
        # in `vw` are oversized - the logo then fills the whole side (seen
        # on the box). Hence in this mode horizontal measures come from `vh`.
        self.assertRegex(self.player,
                         r"body\.portrait #idle \.brand \{[^}]*clamp\([^)]*vh")
        self.assertRegex(self.player,
                         r"body\.portrait #idle \.qrcard img \{[^}]*clamp\([^)]*vh")
        self.assertIn("justify-content: space-evenly", self.player)

    def test_both_rotation_directions_have_their_angle(self):
        # A screen can be hung either way; without the second direction the content
        # would be upside down in half of the installations.
        self.assertRegex(self.player, r"body\.portrait \{[^}]*rotate\(90deg\)")
        self.assertRegex(self.player, r"body\.portrait\.flipped \{[^}]*rotate\(270deg\)")
        self.assertIn("classList.toggle('flipped', fitMode === 'portrait270')", self.player)

    def test_orientation_has_rules_for_both_non_default_options(self):
        self.assertIn("body.portrait", self.player)
        self.assertRegex(self.player, r"body\.fill[^\n]*::after\s*\{[^}]*background-size:\s*cover")

    def test_player_applies_option_from_playlist(self):
        self.assertIn("classList.toggle('portrait', fitMode === 'portrait' || fitMode === 'portrait270')",
                      self.player)
        self.assertIn("classList.toggle('fill', fitMode === 'fill')", self.player)

    def test_viewport_aligned_to_physical_pixels(self):
        # The box reports 1920x1080 at 320 dpi (dpr 2) -> WebView would lay out at 960 CSS
        # px and scale 2x, so the px ceilings in clamp() outgrow the picture and the
        # idle screen overflows (measured on the box).
        self.assertIn("window.devicePixelRatio", self.player)
        self.assertIn("initial-scale=' + (1 / d)", self.player)
        self.assertIn("d > 1.05 && physicalWidth >= 1280", self.player)

    def test_no_fullscreen_hint_in_kiosk(self):
        # The kiosk does not use the Fullscreen API, so isFs() would never
        # detect fullscreen and the hint would stay lit over the ad (seen on the box).
        self.assertIn("function isWholeSurface()", self.player)
        self.assertIn("if (!isWholeSurface()) document.body.appendChild(fsHint)", self.player)
        self.assertIn("if (isFullScreenNow()) fsHint.remove()", self.player)

    def test_sound_tried_only_after_video_starts(self):
        # If sound were attempted already at start, the browser would block
        # playback and leave a black area - hence only after 'playing'.
        self.assertIn("function tryUnmutedWithoutGesture(v)", self.player)
        self.assertIn("if (it.sound && !soundOn) tryUnmutedWithoutGesture(v);", self.player)
        order_playing = self.player.index("ok = true; markOk();")
        order_sound = self.player.index("if (it.sound && !soundOn) tryUnmutedWithoutGesture(v);")
        self.assertLess(order_playing, order_sound)


class HelpersTest(unittest.TestCase):
    def test_safe_prevents_path_traversal(self):
        self.assertEqual(safe("../../etc/passwd"), "passwd")
        self.assertEqual(safe(".."), "file")
        self.assertEqual(safe("a b.jpg"), "a_b.jpg")

    def test_media_path_cannot_escape_root_via_symlink(self):
        old_media = tvserver.MEDIA
        try:
            with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
                tvserver.MEDIA = root
                with open(os.path.join(root, "bezpecne.mp4"), "wb"):
                    pass
                outside_file = os.path.join(outside, "tajne.mp4")
                with open(outside_file, "wb"):
                    pass
                os.symlink(outside_file, os.path.join(root, "unik.mp4"))
                self.assertIsNotNone(media_path("bezpecne.mp4"))
                self.assertIsNone(media_path("unik.mp4"))
                self.assertIsNone(media_path("../tajne.mp4"))
        finally:
            tvserver.MEDIA = old_media

    def test_kind_by_extension(self):
        self.assertEqual(kind("x.MP4"), "video")
        self.assertEqual(kind("x.jpeg"), "image")
        self.assertIsNone(kind("x.txt"))

    def test_env_int_survives_junk(self):
        import os
        os.environ["TEST_ENV_INT"] = "nesmysl"
        self.assertEqual(_env_int("TEST_ENV_INT", 7), 7)
        os.environ["TEST_ENV_INT"] = "-5"
        self.assertEqual(_env_int("TEST_ENV_INT", 7), 7)
        os.environ["TEST_ENV_INT"] = "12"
        self.assertEqual(_env_int("TEST_ENV_INT", 7), 12)
        del os.environ["TEST_ENV_INT"]


class CorsPolicyTest(unittest.TestCase):
    def test_cors_is_fail_closed_and_allows_exact_origin(self):
        old_origin = tvserver.CORS_ORIGIN
        try:
            tvserver.CORS_ORIGIN = ""
            self.assertEqual(tvserver._cors_headers("https://panel.example"), {})
            tvserver.CORS_ORIGIN = "https://panel.example"
            self.assertEqual(tvserver._cors_headers(""), {})
            self.assertEqual(tvserver._cors_headers("https://panel.example.evil"), {})
            self.assertEqual(
                tvserver._cors_headers("https://panel.example"),
                {"Access-Control-Allow-Origin": "https://panel.example",
                 "Vary": "Origin"},
            )
        finally:
            tvserver.CORS_ORIGIN = old_origin


class StatePersistenceTest(unittest.TestCase):
    def test_normalization_filters_corrupted_state(self):
        raw = {
            "devices": {
                "screen": {"version": "nesmysl", "items": [
                    {"file": "ok.mp4", "seconds": "nesmysl"},
                    {"file": "../tajne.mp4"},
                    "neplatna polozka",
                ]},
                "../cizi": {"version": 4, "items": []},
            },
            "pairs": {
                "ABC234": {"client": "client-1234567890", "expires": "42"},
                "spatne": {"client": "client-1234567890", "expires": 42},
            },
        }
        state = tvserver._normalize_state(raw)
        self.assertEqual(set(state["devices"]), {"screen"})
        self.assertEqual(len(state["devices"]["screen"]["items"]), 1)
        item = state["devices"]["screen"]["items"][0]
        self.assertEqual(item["type"], "video")
        self.assertEqual(item["seconds"], 10)
        self.assertEqual(set(state["pairs"]), {"ABC234"})

    def test_legacy_state_migrates_to_schema_1(self):
        old_state_path = tvserver.STATE
        old_state = tvserver._state
        old_recovery = tvserver._state_recovery_required
        try:
            with tempfile.TemporaryDirectory() as root:
                tvserver.STATE = os.path.join(root, "state.json")
                with open(tvserver.STATE, "w", encoding="utf-8") as fh:
                    json.dump({"devices": {"screen": {"version": 3, "items": []}},
                               "pairs": {}}, fh)
                tvserver.load()
                self.assertEqual(tvserver._state["schema_version"], 1)
                tvserver.save()
                with open(tvserver.STATE, encoding="utf-8") as fh:
                    self.assertEqual(json.load(fh)["schema_version"], 1)
        finally:
            tvserver.STATE = old_state_path
            tvserver._state = old_state
            tvserver._state_recovery_required = old_recovery

    def test_corrupted_state_is_backed_up_and_retention_works(self):
        old_state_path = tvserver.STATE
        old_retention = tvserver.STATE_BACKUP_RETENTION
        old_state = tvserver._state
        old_recovery = tvserver._state_recovery_required
        try:
            with tempfile.TemporaryDirectory() as root:
                tvserver.STATE = os.path.join(root, "state.json")
                tvserver.STATE_BACKUP_RETENTION = 2
                original = b'{"devices": '
                with open(tvserver.STATE, "wb") as fh:
                    fh.write(original)
                tvserver.load()
                backups = tvserver._state_backup_paths()
                self.assertEqual(len(backups), 1)
                with open(backups[0], "rb") as fh:
                    self.assertEqual(fh.read(), original)
                for _ in range(3):
                    with open(tvserver.STATE, "wb") as fh:
                        fh.write(original)
                    tvserver.load()
                self.assertEqual(len(tvserver._state_backup_paths()), 2)
                self.assertEqual(tvserver._state["devices"], {})
        finally:
            tvserver.STATE_BACKUP_RETENTION = old_retention
            tvserver.STATE = old_state_path
            tvserver._state = old_state
            tvserver._state_recovery_required = old_recovery

    def test_unreadable_state_is_not_overwritten(self):
        old_state_path = tvserver.STATE
        old_state = tvserver._state
        old_recovery = tvserver._state_recovery_required
        try:
            with tempfile.TemporaryDirectory() as root:
                tvserver.STATE = os.path.join(root, "state.json")
                os.mkdir(tvserver.STATE)
                tvserver.load()
                self.assertTrue(os.path.isdir(tvserver.STATE))
                self.assertEqual(tvserver._state["devices"], {})
                with self.assertRaises(OSError):
                    tvserver.save()
                self.assertTrue(os.path.isdir(tvserver.STATE))
        finally:
            tvserver.STATE = old_state_path
            tvserver._state = old_state
            tvserver._state_recovery_required = old_recovery

    def test_save_and_load_use_atomic_state(self):
        old_state_path = tvserver.STATE
        old_state = tvserver._state
        old_recovery = tvserver._state_recovery_required
        try:
            with tempfile.TemporaryDirectory() as root:
                tvserver.STATE = os.path.join(root, "state.json")
                tvserver._state = {"devices": {"screen": {"version": 3, "items": []}},
                                   "pairs": {}}
                tvserver.save()
                self.assertTrue(os.path.isfile(tvserver.STATE))
                self.assertEqual(os.listdir(root), ["state.json"])
                tvserver._state = {}
                tvserver.load()
                self.assertEqual(tvserver._state["devices"]["screen"]["version"], 3)
        finally:
            tvserver.STATE = old_state_path
            tvserver._state = old_state
            tvserver._state_recovery_required = old_recovery

    def test_upload_reservation_handles_collision_and_quota(self):
        old_media = tvserver.MEDIA
        old_limit = tvserver.MAX_TOTAL_BYTES
        old_names = tvserver._uploading_names
        old_bytes = tvserver._uploading_bytes
        try:
            with tempfile.TemporaryDirectory() as root:
                tvserver.MEDIA = root
                tvserver.MAX_TOTAL_BYTES = 100
                tvserver._uploading_names = set()
                tvserver._uploading_bytes = 0
                first, reason = tvserver._reserve_upload("spot.mp4", 10)
                second, second_reason = tvserver._reserve_upload("spot.mp4", 10)
                blocked, blocked_reason = tvserver._reserve_upload("dalsi.mp4", 81)
                self.assertIsNotNone(first)
                self.assertEqual(reason, None)
                self.assertEqual(second[0], "spot_1.mp4")
                self.assertIsNone(blocked)
                self.assertEqual(blocked_reason, "quota")
                self.assertIsNone(second_reason)
                tvserver._release_upload(first[0], 10)
                tvserver._release_upload(second[0], 10)
        finally:
            tvserver.MEDIA = old_media
            tvserver.MAX_TOTAL_BYTES = old_limit
            tvserver._uploading_names = old_names
            tvserver._uploading_bytes = old_bytes


class AutoRepackPolicyTest(unittest.TestCase):
    """The "repack?" decision must not depend on whether the machine has ffmpeg."""

    def test_quicktime_wrapper_is_detected(self):
        self.assertEqual(_repack_reason("a.mov", qtmov(b"avc1")), "QuickTime obal")

    def test_moov_after_data_is_detected(self):
        self.assertEqual(_repack_reason("a.mp4", mp4_moov_at_end()),
                         "index moov az za daty")

    def test_small_file_without_faststart_is_handled_too(self):
        # warnings forgive small files, but repacking is so cheap there is no reason to skip
        self.assertIsNotNone(_repack_reason("a.mp4", mp4_moov_at_end(mb=0)))

    def test_correct_file_is_left_alone(self):
        self.assertIsNone(_repack_reason("a.mp4", mp4_faststart()))
        self.assertTrue(_container_ok(mp4_faststart()))

    def test_h265_is_not_repacked(self):
        # -c copy would only carry a wrong codec into a "valid" mp4 and the
        # screen would go black without anyone knowing
        self.assertIsNone(_repack_reason("a.mov", qtmov(b"hvc1")))
        self.assertIsNone(_repack_reason("a.mp4", mp4(b"hev1")))

    def test_non_video_media_left_alone(self):
        self.assertIsNone(_repack_reason("a.png", png(100, 100)))
        self.assertIsNone(_repack_reason("a.webm", WEBM))

    def test_invalid_container_is_left_alone(self):
        self.assertIsNone(_repack_reason("a.mp4", b"tohle neni mp4" * 10))


class AutoRepackNoFfmpegTest(unittest.TestCase):
    """A machine without ffmpeg must behave exactly as before auto-convert was introduced."""

    def setUp(self):
        self._ffmpeg = video_convert.FFMPEG
        video_convert.FFMPEG = None

    def tearDown(self):
        video_convert.FFMPEG = self._ffmpeg

    def test_returns_input_unchanged(self):
        data = qtmov(b"avc1")
        name, out, edits = auto_repack("a.mov", data)
        self.assertEqual(name, "a.mov")
        self.assertIs(out, data)
        self.assertEqual(edits, [])

    def test_validation_then_rejects_as_before(self):
        data = qtmov(b"avc1")
        name, out, _ = auto_repack("a.mov", data)
        self.assertIn("QuickTime", validate_media(name, out))

    def test_repack_returns_none(self):
        self.assertIsNone(video_convert.repack(qtmov(b"avc1"), ".mov"))
        self.assertFalse(video_convert.available())


@unittest.skipUnless(video_convert.available(), "ffmpeg is not available on this machine")
class AutoRepackWithFfmpegTest(unittest.TestCase):
    """Real repacking. The test file is generated so the test is self-contained."""

    @classmethod
    def setUpClass(cls):
        import os
        import subprocess
        import tempfile
        cls.tmp = tempfile.mkdtemp(prefix="test-autorepack-")
        cls.mov = os.path.join(cls.tmp, "vzorek.mov")
        subprocess.run(
            [video_convert.FFMPEG, "-nostdin", "-v", "error", "-y",
             "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=1",
             "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-shortest",
             "-c:v", "libx264", "-profile:v", "baseline", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-f", "mov", cls.mov],
            stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=120)
        with open(cls.mov, "rb") as f:
            cls.data = f.read()

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_sample_has_exactly_the_same_defect_as_client_file(self):
        # if ffmpeg ever started generating a correct wrapper right away, the test
        # would silently stop verifying anything - hence the assumption is checked
        self.assertFalse(_container_ok(self.data))
        self.assertEqual(_repack_reason("vzorek.mov", self.data), "QuickTime obal")
        self.assertIn("QuickTime", validate_media("vzorek.mov", self.data))

    def test_repacked_file_passes_validation(self):
        name, out, edits = auto_repack("vzorek.mov", self.data)
        self.assertEqual(name, "vzorek.mp4")
        self.assertTrue(_container_ok(out))
        self.assertIsNone(validate_media(name, out))
        self.assertEqual(len(edits), 1)
        self.assertIn("repackaged", edits[0])

    def test_repacking_is_lossless(self):
        import os
        import subprocess
        _, out, _ = auto_repack("vzorek.mov", self.data)
        target = os.path.join(self.tmp, "prebaleno.mp4")
        with open(target, "wb") as f:
            f.write(out)

        def md5_track(path, track):
            r = subprocess.run(
                [video_convert.FFMPEG, "-nostdin", "-v", "error", "-i", path,
                 "-map", track, "-c", "copy", "-f", "md5", "-"],
                stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=120)
            return r.stdout.strip()

        self.assertEqual(md5_track(self.mov, "0:v:0"), md5_track(target, "0:v:0"))
        self.assertEqual(md5_track(self.mov, "0:a:0"), md5_track(target, "0:a:0"))

    def test_correct_file_stays_byte_for_byte_identical(self):
        _, done, _ = auto_repack("vzorek.mov", self.data)
        name, again, edits = auto_repack("vzorek.mp4", done)
        self.assertEqual(name, "vzorek.mp4")
        self.assertIs(again, done)
        self.assertEqual(edits, [])

    def test_corrupted_data_inside_mdat_does_not_prevent_repack(self):
        # frames overwritten with zeros do not break the container, so repacking works -
        # auto-convert must not pretend to check content it does not check
        broken = self.data[:200] + b"\x00" * 400 + self.data[600:]
        name, out, edits = auto_repack("rozbity.mov", broken)
        self.assertEqual(name, "rozbity.mp4")
        self.assertTrue(_container_ok(out))
        self.assertEqual(len(edits), 1)

    def test_truncated_file_passes_unchanged(self):
        # in a .mov the moov sits at the very end, so a truncated file can no longer
        # be demuxed - ffmpeg fails and the upload must carry on as before
        truncated = self.data[:len(self.data) // 2]
        self.assertEqual(_repack_reason("useknuty.mov", truncated), "QuickTime obal")
        name, out, edits = auto_repack("useknuty.mov", truncated)
        self.assertEqual(name, "useknuty.mov")
        self.assertIs(out, truncated)
        self.assertEqual(edits, [])
        self.assertIsNotNone(validate_media(name, out))


class H264SupportTest(unittest.TestCase):
    """A browser without H.264 rejects every video - the server must know."""

    def setUp(self):
        _online.pop("h264dev", None)

    def tearDown(self):
        _online.pop("h264dev", None)

    def test_report_reads_value(self):
        self.assertIs(_render_report({"h264": ["1"]})["h264"], True)
        self.assertIs(_render_report({"h264": ["0"]})["h264"], False)

    def test_missing_or_nonsense_value_is_unknown(self):
        # an older player does not send the parameter yet - that is not "cannot"
        self.assertIsNone(_render_report({"ok": ["1"]})["h264"])
        self.assertIsNone(_render_report({"h264": ["nesmysl"]})["h264"])
        self.assertIsNone(_render_report({"h264": [""]})["h264"])

    def test_value_is_stored_and_survives_heartbeat_without_it(self):
        touch_device("h264dev", {}, _render_report({"h264": ["0"], "ok": ["0"]}))
        self.assertIs(_online["h264dev"]["h264"], False)
        # the next heartbeat from an older player must not erase the finding
        touch_device("h264dev", {}, _render_report({"ok": ["0"]}))
        self.assertIs(_online["h264dev"]["h264"], False)

    def test_browser_may_change_its_mind(self):
        touch_device("h264dev", {}, _render_report({"h264": ["0"], "ok": ["0"]}))
        touch_device("h264dev", {}, _render_report({"h264": ["1"], "ok": ["1"]}))
        self.assertIs(_online["h264dev"]["h264"], True)

    def test_without_heartbeat_stays_unknown(self):
        touch_device("h264dev", {})
        self.assertIsNone(_online["h264dev"]["h264"])

    def test_storage_quota_is_read_and_stored(self):
        self.assertEqual(_render_report({"quota": ["37/512"]})["quota"], "37/512")
        touch_device("h264dev", {}, _render_report({"quota": ["37/512"], "ok": ["0"]}))
        self.assertEqual(_online["h264dev"]["quota"], "37/512")

    def test_nonsense_quota_is_discarded(self):
        # the value comes from the browser, i.e. from outside - junk must not leak into the API
        for junk in ("", "abc", "1/2/3", "<script>", "-1/5"):
            self.assertEqual(_render_report({"quota": [junk]})["quota"], "", junk)

    def test_quota_survives_heartbeat_without_it(self):
        touch_device("h264dev", {}, _render_report({"quota": ["37/512"], "ok": ["0"]}))
        touch_device("h264dev", {}, _render_report({"ok": ["1"]}))
        self.assertEqual(_online["h264dev"]["quota"], "37/512")


class RangeTest(unittest.TestCase):
    def test_valid_ranges_including_suffix(self):
        self.assertEqual(_range_bounds(None, 100), (0, 99, False))
        self.assertEqual(_range_bounds("bytes=10-19", 100), (10, 19, True))
        self.assertEqual(_range_bounds("bytes=90-", 100), (90, 99, True))
        self.assertEqual(_range_bounds("bytes=-10", 100), (90, 99, True))
        self.assertEqual(_range_bounds("bytes=90-999", 100), (90, 99, True))

    def test_invalid_or_out_of_range(self):
        for value in ("bytes=100-", "bytes=20-10", "bytes=-0",
                      "bytes=abc", "bytes=1-2,4-5"):
            with self.subTest(value=value):
                self.assertIsNone(_range_bounds(value, 100))


if __name__ == "__main__":
    unittest.main()
