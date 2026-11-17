"""The server refuses to start with the placeholder password from .env.example."""
import os
import subprocess
import sys
import unittest

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(APP_DIR))


def _run(password):
    env = dict(os.environ, SIGNAGE_ADMIN_USER="admin", SIGNAGE_ADMIN_PASS=password,
               PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "tvserver.py", "0"], cwd=APP_DIR, env=env,
                          capture_output=True, text=True, timeout=20)


class PlaceholderPasswordTest(unittest.TestCase):
    def test_example_value_is_refused(self):
        r = _run("change-me-to-a-long-random-password")
        self.assertEqual(r.returncode, 2)
        self.assertIn("placeholder", r.stderr)

    def test_placeholder_matches_env_example(self):
        with open(os.path.join(ROOT, ".env.example")) as f:
            text = f.read()
        self.assertIn("SIGNAGE_ADMIN_PASS=change-me-to-a-long-random-password", text)


if __name__ == "__main__":
    unittest.main()
