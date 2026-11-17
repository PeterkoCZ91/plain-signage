# -*- coding: utf-8 -*-
"""Isolates tests from the application's real data.

tvserver reads SIGNAGE_STATE / SIGNAGE_MEDIA / SIGNAGE_TELEMETRY at import; without
them pytest would write telemetry.jsonl (and possibly state and media) straight into
the application directory. Hence:
  1. env variables (incl. SIGNAGE_KIOSK_DIR) point to a temp directory already when conftest is imported,
  2. every test gets its own STATE/MEDIA/TELEMETRY via monkeypatch,
  3. at the end of the run it checks that files in the application directory are unchanged.
"""
import os
import shutil
import tempfile

import pytest

APP_DIR = os.path.dirname(os.path.abspath(__file__))
_SESSION_TMP = tempfile.mkdtemp(prefix="signage-pytest-")
os.environ["SIGNAGE_STATE"] = os.path.join(_SESSION_TMP, "state.json")
os.environ["SIGNAGE_MEDIA"] = os.path.join(_SESSION_TMP, "media")
os.environ["SIGNAGE_TELEMETRY"] = os.path.join(_SESSION_TMP, "telemetry.jsonl")
os.environ["SIGNAGE_KIOSK_DIR"] = os.path.join(_SESSION_TMP, "kiosk")

_GUARDED = ("telemetry.jsonl", "tv-state.json", "tv-state.json.tmp")


def _snapshot():
    out = {}
    for name in _GUARDED:
        try:
            st = os.stat(os.path.join(APP_DIR, name))
            out[name] = (st.st_size, st.st_mtime_ns)
        except OSError:
            out[name] = None
    try:
        out["tv-media"] = sorted(os.listdir(os.path.join(APP_DIR, "tv-media")))
    except OSError:
        out["tv-media"] = None
    return out


_BEFORE = _snapshot()


@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path_factory, monkeypatch):
    import tvserver
    base = tmp_path_factory.mktemp("iso")
    media = base / "media"
    media.mkdir()
    monkeypatch.setattr(tvserver, "STATE", str(base / "state.json"))
    monkeypatch.setattr(tvserver, "MEDIA", str(media))
    monkeypatch.setattr(tvserver, "TELEMETRY", str(base / "telemetry.jsonl"))
    monkeypatch.setattr(tvserver, "KIOSK_DIR", str(base / "kiosk"))
    yield


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_SESSION_TMP, ignore_errors=True)
    after = _snapshot()
    if after != _BEFORE:
        changed = [k for k in after if after[k] != _BEFORE[k]]
        session.exitstatus = 1
        print("\nERROR: tests changed data in the application directory: " + ", ".join(changed))
