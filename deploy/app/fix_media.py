# -*- coding: utf-8 -*-
"""One-off container repair for media that already sit on disk.

Auto-conversion (auto_repack in tvserver) runs only on upload, so files
uploaded earlier stay defective forever - exactly what happened to a client:
the playlist held a QuickTime .mov from before validation was introduced and
no check ever caught it.

The script walks the media directory and repackages a bad container IN PLACE.
It deliberately does not rename .mov to .mp4 even though it is MP4 inside -
the playlist references files by name and a rename would orphan the item. The
server serves .mov as video/mp4 anyway.

Safety rules:
  * the overwrite is atomic (write to .tmp + os.replace), so the player never
    loads a half-written file
  * before overwriting it verifies the video track comes out BIT-identical -
    if not, the file is left alone
  * without ffmpeg the script only reports what it would do

Usage (on the VPS, inside the container that has ffmpeg):
    docker exec signage python3 fix_media.py --dry-run
    docker exec signage python3 fix_media.py
"""
import os
import subprocess
import sys
import tempfile

import video_convert
from tvserver import MEDIA, _container_ok, _repack_reason

VIDEO_EXT = (".mp4", ".m4v", ".mov")


def _md5_tracks(path, track):
    """MD5 of the raw packets of one track - changes only on a real re-encode."""
    try:
        r = subprocess.run(
            [video_convert.FFMPEG, "-nostdin", "-v", "error", "-i", path,
             "-map", track, "-c", "copy", "-f", "md5", "-"],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None                              # the track does not exist (e.g. no audio)
    return r.stdout.strip() or None


def _is_lossless(original, new_path):
    """True if both video and audio come out bit-identical to the original."""
    v_before, v_after = _md5_tracks(original, "0:v:0"), _md5_tracks(new_path, "0:v:0")
    if not v_before or v_before != v_after:
        return False
    # audio need not exist; if it was there, it must stay the same
    a_before, a_after = _md5_tracks(original, "0:a:0"), _md5_tracks(new_path, "0:a:0")
    return a_before == a_after


def repair_file(path, dry_run=False):
    """Return (changed, description) for one file."""
    name = os.path.basename(path)
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        return False, "%s: cannot read (%s)" % (name, e)

    reason = _repack_reason(name, data)
    if not reason:
        return False, None                       # fine, stay silent
    if dry_run:
        return False, "%s: WOULD REPACK (%s)" % (name, reason)
    if not video_convert.available():
        return False, "%s: %s, but ffmpeg is not here" % (name, reason)

    repacked = video_convert.repack(data, os.path.splitext(name)[1].lower())
    if not repacked or not _container_ok(repacked):
        return False, "%s: %s, but repacking failed - keeping the original" % (name, reason)

    # write alongside, verify losslessness, only then swap
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".oprava-", suffix=".mp4")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(repacked)
        if not _is_lossless(path, tmp):
            return False, "%s: repacking would alter the track - NOT overwriting" % name
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)                   # atomic
        tmp = None
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
    return True, "%s: FIXED (%s), %.1f -> %.1f MB" % (
        name, reason, len(data) / 1048576, len(repacked) / 1048576)


def main(argv):
    dry_run = "--dry-run" in argv
    if not os.path.isdir(MEDIA):
        print("media directory does not exist: %s" % MEDIA)
        return 1
    print("ffmpeg: %s" % (video_convert.ffmpeg_version() or "MISSING"))
    print("directory: %s%s\n" % (MEDIA, "   (DRY RUN)" if dry_run else ""))

    changed = remaining = 0
    for name in sorted(os.listdir(MEDIA)):
        if not name.lower().endswith(VIDEO_EXT):
            continue
        ok, desc = repair_file(os.path.join(MEDIA, name), dry_run)
        if desc:
            print("  " + desc)
        if ok:
            changed += 1
        elif desc:
            remaining += 1                           # reported but not fixed
    print("\ndone: %d fixed, %d remaining" % (changed, remaining))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
