# -*- coding: utf-8 -*-
"""Auto-conversion of uploaded videos - the mechanics of calling ffmpeg.

Rule: NEVER re-encode when repackaging is enough. A client's .mov from a phone
or a chat often has a perfectly fine H.264 track, it just carries it in a
QuickTime container with the moov index after the data - the browser then
waits for the whole file to download before showing the first frame. Measured
on a real sample file (22.9 MB, 21 s, 720x1280 H.264 Main): repackaging 0.13 s
and the video track comes out bit-identical, a full re-encode 9.8 s and a
loss of quality.

This module deliberately knows nothing about MP4 boxes - the decision "is a
conversion needed?" belongs in tvserver.py, here is only the ffmpeg launching.
No function raises: if the conversion fails it returns None and the caller
carries on with the original data, so a server without ffmpeg (or with a
damaged file) behaves exactly as before auto-conversion was introduced.
"""
import os
import shutil
import subprocess
import tempfile
import threading

# ffmpeg is optional. The image has it, but a development machine or an older
# deployment may not - the server then simply does not fix containers and
# rejects them as before.
FFMPEG = shutil.which("ffmpeg")

CONVERT_TIMEOUT_S = 180        # a damaged file must not hold a thread forever
CONVERT_SLOTS = 2              # how many conversions may run at once (VPS RAM + CPU)
CONVERT_WAIT_S = 5           # how long an upload waits for a free slot (then 503 + Retry-After)

_slots = threading.Semaphore(CONVERT_SLOTS)


def available():
    """True if ffmpeg is on the machine and auto-conversion can run."""
    return bool(FFMPEG)


def ffmpeg_version():
    """First line of `ffmpeg -version`, or None. Only for /api/health."""
    if not FFMPEG:
        return None
    try:
        r = subprocess.run([FFMPEG, "-version"], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=10)
        return r.stdout.decode("utf-8", "replace").splitlines()[0][:120] or None
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


def repack(data, ext=".mp4"):
    """Repackage a video into MP4 with faststart WITHOUT re-encoding (-c copy).

    Returns the new data, or None if it cannot be done: ffmpeg is missing, the
    file is damaged, the codec does not fit into MP4, or the conversion timed out.
    """
    if not FFMPEG:
        return None
    if not _slots.acquire(timeout=CONVERT_WAIT_S):
        return None                              # the server is swamped, better do nothing
    tmp = None
    try:
        tmp = tempfile.mkdtemp(prefix="signage-convert-")
        src = os.path.join(tmp, "input" + ext)
        dst = os.path.join(tmp, "output.mp4")
        with open(src, "wb") as f:
            f.write(data)
        r = subprocess.run(
            [FFMPEG, "-nostdin", "-v", "error", "-y", "-i", src,
             # only the first video and an optional audio track: phones also add
             # a timecode track the browser does not expect
             "-map", "0:v:0", "-map", "0:a?",
             "-c", "copy",                       # lossless, no re-encoding
             "-movflags", "+faststart",          # moov index before the data
             dst],
            stdin=subprocess.DEVNULL, capture_output=True,
            timeout=CONVERT_TIMEOUT_S)
        if r.returncode != 0:
            return None
        with open(dst, "rb") as f:
            out = f.read()
        return out or None
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        _slots.release()
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
