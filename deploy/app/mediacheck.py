"""Content checks for uploaded files: magic bytes, image and video dimensions, faststart, hard limits (pure functions over bytes)."""
import os

from errors import ErrText


# --- validation of uploaded file content ------------------------------------
# A bad codec = black screen on the TV; better to reject right at upload.
_IMG_MAGIC = {
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".gif": (b"GIF87a", b"GIF89a"),
}
BAD_VIDEO_CODECS = {b"hev1", b"hvc1", b"av01"}
_CODEC_NAMES = {b"hev1": "H.265", b"hvc1": "H.265", b"av01": "AV1"}
# Brands in ftyp that mean ISO BMFF (= MP4 that <video> on the TV accepts).
# Pure QuickTime has only "qt  " and does not play in the panel's browser.
MP4_BRANDS = {b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42",
              b"avc1", b"M4V ", b"M4A ", b"dash", b"3gp4", b"3gp5", b"3g2a"}
_MP4_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl"}
_MP4_TOP_BOXES = (b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide")
# NOTE: these are NOT product limits, only the threshold above which we warn.
# The service also drives modern large TVs, where a 4K spot is a legitimate
# request - rejecting it would be wrong. Older panels (Samsung DB10D) decode
# at most 1920x1080 (H.264 Level 4.1) and show a black screen above that.
# When the device itself reports its canvas size (heartbeat), that is used for
# the judgement; these values are only a fallback until the screen has reported.
# A rotated 1080x1920 is still just "FullHD in portrait", so the longer and
# shorter side are compared, not width and height.
SAFE_VIDEO_LONG = 1920
SAFE_VIDEO_SHORT = 1080
# A photo is drawn twice on the screen (layer + blurred background). Above
# SAFE_IMAGE_MPX it only warns; HARD_* is a bomb no browser can fit into
# memory - there it is rejected.
SAFE_IMAGE_MPX = 25
HARD_IMAGE_MPX = 100
MAX_IMAGE_SIDE = 20000
FASTSTART_MIN_BYTES = 5 * 1024 * 1024   # below this we do not care about faststart
JPEG_HEADER_SCAN = 4 * 1024 * 1024      # how many bytes of JPEG headers to scan at most
# SOFn markers carrying dimensions (without C4 = DHT, C8 = JPG and CC = DAC).
_JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
             0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _mp4_brands(data):
    """Return the brands from the ftyp box (major + compatible), or an empty set."""
    if len(data) < 16 or data[4:8] != b"ftyp":
        return set()                        # ftyp missing -> cannot determine the brand
    size = int.from_bytes(data[0:4], "big")
    if size < 16:
        size = 16
    head = data[8:min(size, len(data))]     # major brand + version + compatible
    return {head[i:i + 4] for i in range(0, len(head) - 3, 4)} - {head[4:8]}


def _mp4_boxes(data):
    """Walk the MP4/MOV box tree and yield (type, content start, box end)."""
    stack = [(0, len(data))]
    while stack:
        pos, end = stack.pop()
        while pos + 8 <= end:
            size = int.from_bytes(data[pos:pos + 4], "big")
            typ = data[pos + 4:pos + 8]
            head = 8
            if size == 1:                       # 64-bit size (largesize)
                if pos + 16 > end:
                    break
                size = int.from_bytes(data[pos + 8:pos + 16], "big")
                head = 16
            elif size == 0:                     # box extends to the end of the file
                size = end - pos
            if size < head or pos + size > end:
                break
            if typ in _MP4_CONTAINERS:
                stack.append((pos + head, pos + size))
            yield typ, pos + head, pos + size
            pos += size


def _stsd_entries(data, start, end):
    """Yield (fourcc, start, end) of each sample entry in a stsd box."""
    entry = start + 8                           # version+flags (4B) + count (4B)
    while entry + 8 <= end:
        esize = int.from_bytes(data[entry:entry + 4], "big")
        if esize < 8 or entry + esize > end:
            break
        yield data[entry + 4:entry + 8], entry, entry + esize
        entry += esize


def _mp4_sample_formats(data):
    """Walk the MP4/MOV boxes and return the fourcc of all sample entries from stsd."""
    found = set()
    for typ, start, end in _mp4_boxes(data):
        if typ == b"stsd":
            found.update(f for f, _s, _e in _stsd_entries(data, start, end))
    return found


def _tkhd_size(data, start, end):
    """Track dimensions from tkhd (16.16 fixed). An audio track has zeros."""
    if start >= len(data):
        return None
    version = data[start]
    # version+flags (4B) + times and id (20B, or 32B for version 1) + 16B header
    # (reserved, layer, group, volume) + 36B transformation matrix
    off = start + 4 + (32 if version == 1 else 20) + 16 + 36
    if off + 8 > end:
        return None
    w = int.from_bytes(data[off:off + 4], "big")
    h = int.from_bytes(data[off + 4:off + 8], "big")
    return ((w + 0x8000) >> 16, (h + 0x8000) >> 16)


def _visual_entry_size(data, start, end):
    """Dimensions from a VisualSampleEntry in stsd; None for audio and truncated entries."""
    if end - start < 86:                        # 8B header + 8B SampleEntry + 70B
        return None
    if data[start + 84:start + 86] != b"\xff\xff":   # pre_defined = -1 at the end
        return None                             # does not look like a video track
    return (int.from_bytes(data[start + 32:start + 34], "big"),
            int.from_bytes(data[start + 34:start + 36], "big"))


def _biggest(sizes):
    """Pick the dimensions with the most pixels; drop zeros (audio)."""
    real = [s for s in sizes if s and s[0] > 0 and s[1] > 0]
    return max(real, key=lambda wh: wh[0] * wh[1]) if real else None


def _mp4_size(data):
    """Video track dimensions in MP4/MOV, or None if they cannot be read."""
    tracks, samples = [], []
    for typ, start, end in _mp4_boxes(data):
        if typ == b"tkhd":
            tracks.append(_tkhd_size(data, start, end))
        elif typ == b"stsd":
            for _f, estart, eend in _stsd_entries(data, start, end):
                samples.append(_visual_entry_size(data, estart, eend))
    track, sample = _biggest(tracks), _biggest(samples)
    if track is None or sample is None:
        return track or sample          # one of the two values, or None
    # tkhd is the display size (already with pixel aspect ratio), stsd is the
    # actual decoded resolution. When they differ we take the smaller one - the
    # decoder cares about coded pixels and we would rather not reject a valid file.
    return min(track, sample, key=lambda wh: wh[0] * wh[1])


def _jpeg_size(data):
    """Dimensions from the first SOFn marker (the segment holds height first, then width)."""
    # Headers sit at the start of the file; the cap bounds the cost even for
    # broken files, where tens of MB of padding bytes would otherwise be walked.
    pos, n = 2, min(len(data), JPEG_HEADER_SCAN)
    while pos + 3 < n:
        if data[pos] != 0xFF:                   # padding between segments
            pos = data.find(b"\xff", pos)       # jump to the next marker (fast, in C)
            if pos < 0:
                return None
            continue
        marker = data[pos + 1]
        if marker == 0xFF or marker == 0x01 or 0xD0 <= marker <= 0xD9:
            pos += 2                            # markers without length (SOI, RST, EOI)
            continue
        if marker == 0xDA:                      # start of data, headers have ended
            return None
        seg = int.from_bytes(data[pos + 2:pos + 4], "big")
        if seg < 2:
            return None
        if marker in _JPEG_SOF and pos + 9 <= n:
            return (int.from_bytes(data[pos + 7:pos + 9], "big"),
                    int.from_bytes(data[pos + 5:pos + 7], "big"))
        pos += 2 + seg
    return None


def _png_size(data):
    """Dimensions from IHDR, which in PNG always follows the signature."""
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    return (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"))


def _gif_size(data):
    """Dimensions from the logical screen descriptor (little-endian right after GIF89a)."""
    if len(data) < 10:
        return None
    return (int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little"))


def _webp_size(data):
    """Dimensions from the first VP8X / VP8 (lossy) / VP8L (lossless) chunk."""
    pos = 12                                    # after RIFF + length + WEBP
    while pos + 8 <= len(data):
        typ = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        body = pos + 8
        if typ == b"VP8X" and body + 10 <= len(data):
            return (int.from_bytes(data[body + 4:body + 7], "little") + 1,
                    int.from_bytes(data[body + 7:body + 10], "little") + 1)
        if (typ == b"VP8 " and body + 10 <= len(data)
                and data[body + 3:body + 6] == b"\x9d\x01\x2a"):
            return (int.from_bytes(data[body + 6:body + 8], "little") & 0x3FFF,
                    int.from_bytes(data[body + 8:body + 10], "little") & 0x3FFF)
        if typ == b"VP8L" and body + 5 <= len(data) and data[body] == 0x2F:
            bits = int.from_bytes(data[body + 1:body + 5], "little")
            return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
        if size <= 0:
            break
        pos = body + size + (size & 1)          # chunks are padded to an even size
    return None


def _image_size(ext, data):
    """Image dimensions by extension, or None if they cannot be read."""
    reader = {".jpg": _jpeg_size, ".jpeg": _jpeg_size, ".png": _png_size,
              ".gif": _gif_size, ".webp": _webp_size}.get(ext)
    size = reader(data) if reader else None
    return size if size and size[0] > 0 and size[1] > 0 else None



def _top_box_order(data):
    """Top-level box types in the order they appear in the file."""
    order, pos, n = [], 0, len(data)
    while pos + 8 <= n and len(order) <= 64:
        size = int.from_bytes(data[pos:pos + 4], "big")
        typ = data[pos + 4:pos + 8]
        head = 8
        if size == 1:                           # 64-bit size
            if pos + 16 > n:
                break
            size = int.from_bytes(data[pos + 8:pos + 16], "big")
            head = 16
        elif size == 0:                         # box to the end of the file
            order.append(typ)
            break
        if size < head:
            break
        order.append(typ)
        pos += size
    return order


def _moov_after_mdat(data):
    """True if the moov index sits after mdat (= the player waits for the whole file).

    Regardless of file size - unlike the warning, which forgives small files.
    Auto-conversion fixes it so cheaply that there is no point waiting for a threshold.
    """
    order = _top_box_order(data)
    if b"moov" not in order or b"mdat" not in order:
        return False
    return order.index(b"moov") > order.index(b"mdat")


def _faststart_warning(data):
    """Warn when the moov index is after mdat (the player waits for the whole file)."""
    if len(data) < FASTSTART_MIN_BYTES:
        return None                             # not a problem for small files
    if not _moov_after_mdat(data):
        return None
    return ErrText("warn_no_faststart", mb=len(data) // (1024 * 1024))


def _image_hard_limit(size):
    """Error only for an image that no browser can fit into memory."""
    if not size:
        return None                             # not measured = we forbid nothing
    w, h = size
    if max(w, h) > MAX_IMAGE_SIDE:
        return ErrText("media_image_side", w=w, h=h, max_side=MAX_IMAGE_SIDE)
    if w * h > HARD_IMAGE_MPX * 1000000:
        mpx = -(-w * h // 100000) / 10.0
        return ErrText("media_image_huge", w=w, h=h, mpx=mpx, max_mpx=HARD_IMAGE_MPX)
    return None


def validate_media(name, data):
    """Check that the file content matches its extension. Returns an error, or None."""
    ext = os.path.splitext(name)[1].lower()
    if ext in _IMG_MAGIC:
        if not any(data.startswith(m) for m in _IMG_MAGIC[ext]):
            return ErrText("media_bad_content", ext=ext.lstrip("."))
        return _image_hard_limit(_image_size(ext, data))
    if ext == ".webp":
        if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
            return ErrText("media_bad_content", ext="webp")
        return _image_hard_limit(_image_size(ext, data))
    if ext == ".webm":
        if not data.startswith(b"\x1a\x45\xdf\xa3"):
            return ErrText("media_bad_webm")
        return None
    if ext in (".mp4", ".m4v", ".mov"):
        if data[4:8] not in _MP4_TOP_BOXES:
            return ErrText("media_bad_mp4")
        brands = _mp4_brands(data)
        if brands and not (brands & MP4_BRANDS):
            return ErrText("media_quicktime")
        bad = _mp4_sample_formats(data) & BAD_VIDEO_CODECS
        if bad:
            return ErrText("media_codec", codec=b"/".join(sorted(bad)).decode(),
                           codec_name=" / ".join(sorted({_CODEC_NAMES[c] for c in bad})))
        return None
    return None
