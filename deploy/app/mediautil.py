"""Small pure media helpers: extensions, file names, Range bounds, mmap view, conversion exceptions."""
import contextlib
import mmap
import os
import re
import secrets

from errors import ErrText


IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")
VID_EXT = (".mp4", ".m4v", ".webm", ".mov")
ALLOWED_EXT = IMG_EXT + VID_EXT


ALLOWED_DESC = ", ".join(ALLOWED_EXT)


# Common formats a client sends that we cannot handle - a generic
# "unsupported type" message would not help, so we say right away what to do.
_HEIC_EXT = (".heic", ".heif")
_VIDEO_EXT_UNSUPPORTED = (".avi", ".mkv", ".wmv", ".flv", ".mts", ".m2ts",
                            ".mpg", ".mpeg", ".3gp")


def unsupported_msg(name):
    """Message for an extension we do not support - with instructions for common formats.

    Returns an ErrText (default Czech text + code/params for localization).
    """
    ext = os.path.splitext(name)[1].lower()
    if ext in _HEIC_EXT:
        return ErrText("heic_unsupported")
    if ext in _VIDEO_EXT_UNSUPPORTED:
        return ErrText("video_format_unsupported", ext=ext[1:])
    return ErrText("unsupported_type", allowed=ALLOWED_DESC)


def kind(name):
    ext = os.path.splitext(name)[1].lower()
    if ext in VID_EXT:
        return "video"
    if ext in IMG_EXT:
        return "image"
    return None


def safe(name):
    base = os.path.basename(name).replace(" ", "_")
    base = re.sub(r"[^A-Za-z0-9._-]", "", base)
    base = base.lstrip(".")            # block "..", "." and hidden paths (path traversal)
    return base or "file"


def _media_name(fname):
    """File name for a new upload with a random prefix.

    /media/<file> is public by name; without the prefix "logo.jpg" would be
    guessable. Old items keep their names (no migration).
    """
    stem, ext = os.path.splitext(safe(fname))
    return f"{secrets.token_hex(8)}_{stem[:100]}{ext}"


def _range_bounds(value, size):
    """Return (start, end, partial), or None for an invalid Range."""
    if not value:
        return 0, max(size - 1, -1), False
    if size <= 0 or not value.startswith("bytes="):
        return None
    spec = value[6:]
    if "," in spec:
        return None
    lo, sep, hi = spec.partition("-")
    if not sep or (not lo and not hi):
        return None
    try:
        if lo:
            start = int(lo)
            if start < 0 or start >= size:
                return None
            end = int(hi) if hi else size - 1
            if end < start:
                return None
            return start, min(end, size - 1), True
        suffix = int(hi)
        if suffix <= 0:
            return None
        return max(0, size - suffix), size - 1, True
    except ValueError:
        return None


# --- processing files on disk (an upload does not hold the file in memory) -----
# The analyzers above work with "bytes" (indexing, slices, find, startswith).
# Instead of loading the whole file we hand them an mmap view: only what they
# touch is read (headers, boxes), the rest stays on disk and off the Python heap.

class _MapView:
    """Read-only mmap with the interface the analyzers need from `bytes`."""

    def __init__(self, mm):
        self._mm = mm

    def __len__(self):
        return len(self._mm)

    def __getitem__(self, key):
        return self._mm[key]

    def startswith(self, prefix):
        return self._mm[:len(prefix)] == prefix

    def find(self, *args):
        return self._mm.find(*args)


@contextlib.contextmanager
def _mapped(path):
    """View of the file content as bytes (empty file = b"")."""
    with open(path, "rb") as f:
        if os.fstat(f.fileno()).st_size == 0:
            yield b""
            return
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            yield _MapView(mm)
        finally:
            mm.close()


class ConvertBusy(Exception):
    """All conversion slots are busy even after a short wait (-> 503)."""


class ConvertNoSpace(Exception):
    """No disk space for the temporary conversion output (-> 507)."""


def _unlink_quiet(path):
    try:
        os.unlink(path)
    except OSError:
        pass
