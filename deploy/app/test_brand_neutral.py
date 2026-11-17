"""The code and the default brand must carry no real contact data.

Fixed neutral patterns, no external list: no real e-mails, phone numbers,
domains outside example.* and no IPs outside documentation ranges
(RFC 5737, RFC 3849) in the code, pages and default brand.
"""
import importlib
import json
import os
import re
import unittest

import brand

APP_DIR = os.path.dirname(os.path.abspath(__file__))

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
PHONE_RE = re.compile(r"(?<![\w.])\+\d{1,3}[ -]?\d{3}[ -]?\d{3}[ -]?\d{3,4}(?!\d)")
URL_HOST_RE = re.compile(r"https?://([A-Za-z0-9.-]+)", re.I)
IPV4_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")
DOC_NETS = ("192.0.2.", "198.51.100.", "203.0.113.")
# hosts that are not contact data (local, standards, libraries, schema namespaces)
ALLOWED_HOSTS = ("localhost", "example.com", "example.org", "example.net", "www.w3.org",
                 "api.telegram.org", "www.instagram.com")


def _scanned_files():
    names = ["brand.py", "tvserver.py", "errors.py", "validators.py"]
    names += [os.path.join("tv", n) for n in ("player.html", "admin.html", "connect.html", "sw.js")]
    names += [os.path.join("tv", "assets", "manifest.webmanifest")]
    return [os.path.join(APP_DIR, n) for n in names]


def _host_ok(host):
    host = host.lower().rstrip(".")
    if not host or "." not in host:
        return True
    return any(host == a or host.endswith("." + a) for a in ALLOWED_HOSTS)


def _findings(text):
    out = []
    for m in EMAIL_RE.finditer(text):
        if not _host_ok(m.group(1)):
            out.append("e-mail " + m.group(0))
    for m in PHONE_RE.finditer(text):
        if set(re.sub(r"\D", "", m.group(0))) != {"0"}:
            out.append("phone " + m.group(0))
    for m in URL_HOST_RE.finditer(text):
        if not _host_ok(m.group(1)) and not IPV4_RE.fullmatch(m.group(1)):
            out.append("domain " + m.group(1))
    for m in IPV4_RE.finditer(text):
        ip = m.group(1)
        parts = [int(x) for x in ip.split(".")]
        if max(parts) > 255 or ip.startswith(DOC_NETS) or ip in ("0.0.0.0", "127.0.0.1"):
            continue
        out.append("ip " + ip)
    return out


def _pristine_defaults():
    """BRAND_DEFAULTS as shipped: no SIGNAGE_BRAND_* in the environment, no brand.env."""
    saved = dict(os.environ)
    for key in list(os.environ):
        if key.startswith("SIGNAGE_BRAND_"):
            del os.environ[key]
    os.environ["SIGNAGE_BRAND_FILE"] = os.path.join(APP_DIR, "no-such-brand.env")
    try:
        return dict(importlib.reload(brand).BRAND_DEFAULTS)
    finally:
        os.environ.clear()
        os.environ.update(saved)
        importlib.reload(brand)


class NeutralDefaultsTest(unittest.TestCase):
    def test_default_brand_is_neutral_demo(self):
        d = _pristine_defaults()
        self.assertTrue(d["email"].endswith("@example.com"), d["email"])
        self.assertIn("example.com", d["url"])
        self.assertEqual(d["name"], "Your Brand")
        self.assertEqual(_findings(json.dumps(d, ensure_ascii=False)), [])

    def test_manifest_is_neutral_json(self):
        with open(os.path.join(APP_DIR, "tv", "assets", "manifest.webmanifest"), encoding="utf-8") as fh:
            m = json.load(fh)
        self.assertEqual(m["short_name"], "Signage")


class NoContactDataTest(unittest.TestCase):
    def test_patterns_catch_what_they_should(self):
        self.assertTrue(_findings("write to someone@company.example"))
        self.assertTrue(_findings("call +420 123 456 789"))
        self.assertTrue(_findings("see https://shop.company.cz/x"))
        self.assertTrue(_findings("host 198.18.0.1"))
        self.assertFalse(_findings("info@example.com https://example.com/ 203.0.113.7 +000 000 000 000"))

    def test_code_and_pages_have_no_real_contact_data(self):
        for path in _scanned_files():
            with open(path, encoding="utf-8") as fh:
                found = _findings(fh.read())
            self.assertEqual(found, [], os.path.basename(path))

    def test_default_brand_has_no_real_contact_data(self):
        blob = json.dumps(_pristine_defaults(), ensure_ascii=False)
        self.assertEqual(_findings(blob), [])


if __name__ == "__main__":
    unittest.main()
