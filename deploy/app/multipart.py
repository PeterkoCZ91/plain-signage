#!/usr/bin/env python3
"""multipart/form-data parser built on the stdlib only (a replacement for the removed cgi module).

The `cgi` module is gone in Python 3.13, so `cgi.FieldStorage` can no longer
be used. This parser reads the request body straight from a file-like object
(in production `self.rfile` from http.server), keeps a hard cap on the number
of bytes read and reports every malformed input as `MultipartError` - never an
unhandled crash.

    parts = parse_multipart(self.rfile, ctype, clen, MAX_TOTAL_BYTES)
    for p in parts:
        if p.filename:
            save(p.filename, p.content)

With `spool_dir` the file content is not accumulated in memory but streamed
into a temporary file (`.signage-upload-*`) in the given directory; `Part`
then carries `path` and `size`. From the return on, the caller owns the file
(`Part.discard()`); if the parser fails, it cleans everything up itself.
"""
import os
import re
import tempfile
from typing import Dict, List, Optional
from urllib.parse import unquote_to_bytes

__all__ = ["MultipartError", "FileTooLargeError", "Part", "parse_multipart",
           "MAX_PARTS", "MAX_HEADER_BYTES", "MAX_PREAMBLE_BYTES", "MAX_FIELD_BYTES",
           "SPOOL_PREFIX"]

MAX_PARTS = 256                   # cap on the number of parts in one body
MAX_HEADER_BYTES = 16 * 1024      # cap on the headers of one part
MAX_PREAMBLE_BYTES = 8 * 1024     # text before the first delimiter (normally empty)
MAX_FIELD_BYTES = 1024 * 1024     # cap on a plain form field (held in RAM)
SPOOL_PREFIX = ".signage-upload-"  # temporary files are known to both the cleanup and the quota in tvserver

_CHUNK = 64 * 1024                # how many bytes we take from the network per read
_COMPACT_AT = 1 << 20             # when to drop the already processed start of the buffer
_TAIL_LOOKAHEAD = 256             # how many bytes after the boundary we inspect (padding + CRLF)

# RFC 2046: a boundary is 1-70 characters from a narrow set and must not end with a space
_BOUNDARY_RE = re.compile(rb"^[0-9A-Za-z'()+_,\-./:=? ]{1,70}$")
_LINE_RE = re.compile(r"\r\n|\n")


def _classify_tail(tail: bytes):
    """Tell whether the delimiter really ends after the boundary: (is_final, tail length)."""
    if tail.startswith(b"--"):
        return True, 2                       # --boundary-- = end of body
    idx = tail.find(b"\r\n")
    if idx >= 0 and not tail[:idx].strip(b" \t"):
        return False, idx + 2                # --boundary [whitespace] CRLF
    return None, 0                           # just a similar sequence inside the data


class MultipartError(Exception):
    """The input is not a valid multipart/form-data body (or exceeds the limits)."""


class FileTooLargeError(MultipartError):
    """A file exceeded max_file_bytes; reading is aborted as soon as that shows."""


class Part:
    """One part of the body: a plain form field or an uploaded file.

    A file streamed to disk (spool_dir) has `path` and `size`; `content` is then
    loaded only on demand. A plain field and a file without spool_dir hold their bytes in RAM.
    """

    def __init__(self, name, filename, content=b"", content_type="",
                 path=None, size=None):
        self.name = name
        self.filename = filename
        self.content_type = content_type
        self.path = path
        self._content = content
        self.size = len(content) if size is None else size

    @property
    def content(self) -> bytes:
        """The whole content. For a file on disk it loads it (careful, it is all held in memory)."""
        if self.path is not None:
            with open(self.path, "rb") as f:
                return f.read()
        return self._content

    @property
    def is_file(self) -> bool:
        """Tell whether the part arrived as a file (has a filename)."""
        return self.filename is not None

    def text(self, encoding: str = "utf-8") -> str:
        """The part content as text - for plain form fields."""
        return self.content.decode(encoding, "replace")

    def discard(self) -> None:
        """Delete the part's temporary file (if there is one)."""
        if self.path is not None:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass

    def __repr__(self) -> str:
        return "Part(name=%r, filename=%r, size=%d, path=%r)" % (
            self.name, self.filename, self.size, self.path)


class _Spool:
    """Lazy temporary file: created on the first write; an empty file creates nothing."""

    def __init__(self, directory):
        self._dir = directory
        self.path = None
        self._f = None
        self.size = 0

    def write(self, data: bytes) -> None:
        if not data:
            return
        if self._f is None:
            fd, self.path = tempfile.mkstemp(prefix=SPOOL_PREFIX, dir=self._dir)
            self._f = os.fdopen(fd, "wb")
        self._f.write(data)
        self.size += len(data)

    def close(self) -> None:
        if self._f is not None:
            self._f.close()
            self._f = None

    def remove(self) -> None:
        self.close()
        if self.path is not None:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
            self.path = None


# --- reading from the network with a hard cap ----------------------------------------

class _Reader:
    """Buffered body reader that never reads more than the given limit."""

    def __init__(self, fp, limit: int):
        self._fp = fp
        self._left = limit          # how many bytes we may still take from the input
        self._buf = bytearray()
        self._pos = 0

    def prime(self, data: bytes) -> None:
        """Insert artificial data before the start of the body (a virtual CRLF before the delimiter)."""
        self._buf.extend(data)

    def available(self) -> int:
        """How many unprocessed bytes already sit in the buffer."""
        return len(self._buf) - self._pos

    def _compact(self) -> None:
        """Drop the already processed start of the buffer so memory does not grow."""
        if self._pos >= _COMPACT_AT:
            del self._buf[:self._pos]
            self._pos = 0

    def _fill(self, want: int) -> bool:
        """Top the buffer up to at least `want` bytes; return False when nothing more arrives."""
        grown = False
        while self.available() < want and self._left > 0:
            chunk = self._fp.read(min(_CHUNK, self._left))
            if not chunk:               # the input ended earlier than Content-Length promised
                self._left = 0
                break
            self._left -= len(chunk)
            self._buf.extend(chunk)
            grown = True
        return grown

    def drain(self) -> None:
        """Read and discard the rest of the body so the keep-alive connection stays in sync."""
        while self._left > 0:
            chunk = self._fp.read(min(_CHUNK, self._left))
            if not chunk:
                self._left = 0
                break
            self._left -= len(chunk)

    def peek(self, n: int) -> bytes:
        """Peek at n bytes without consuming them (a shorter result = end of body)."""
        self._fill(n)
        return bytes(self._buf[self._pos:self._pos + n])

    def skip(self, n: int) -> None:
        """Discard n already peeked bytes."""
        self._pos = min(self._pos + n, len(self._buf))
        self._compact()

    def read_to_delimiter(self, sep: bytes, cap: Optional[int] = None,
                          exc=MultipartError):
        """Return (data before the delimiter, is_final), or None if the delimiter never arrives.

        If the data exceeds cap, raises exc - already during reading, as soon as
        the buffer has outgrown cap (plus a reserve for the delimiter), not only
        after the whole body has been read.
        """
        search = self._pos
        while True:
            idx = self._buf.find(sep, search)
            if idx < 0:
                if cap is not None and self.available() > cap + len(sep) + _TAIL_LOOKAHEAD:
                    raise exc("request part exceeds the allowed limit")
                # the delimiter may be split between two reads - overlap len(sep)-1
                search = max(search, len(self._buf) - len(sep) + 1)
                if not self._fill(self.available() + _CHUNK):
                    return None
                continue

            # A candidate is a delimiter only if a line ends after it (or two
            # dashes follow). Otherwise it is a random sequence in the data.
            tail_at = idx + len(sep)
            self._fill(tail_at - self._pos + _TAIL_LOOKAHEAD)
            final, used = _classify_tail(bytes(self._buf[tail_at:tail_at + _TAIL_LOOKAHEAD]))
            if final is None:
                search = idx + 1
                continue

            if cap is not None and idx - self._pos > cap:
                raise exc("request part exceeds the allowed limit")
            data = bytes(self._buf[self._pos:idx])
            self._pos = tail_at + used
            self._compact()
            return data, final

    def stream_to_delimiter(self, sep: bytes, sink, cap: Optional[int] = None,
                            exc=MultipartError):
        """Like read_to_delimiter, but the data flows into sink(bytes) in chunks.

        Returns (byte_count, is_final), or None if the delimiter never arrives.
        Only a buffer window stays in memory; cap is enforced during reading
        (exceeding it = exc).
        """
        total = 0

        def flush(upto):
            nonlocal total
            n = upto - self._pos
            if n <= 0:
                return
            total += n
            if cap is not None and total > cap:
                raise exc("request part exceeds the allowed limit")
            sink(bytes(self._buf[self._pos:upto]))
            self._pos = upto
            self._compact()

        while True:
            idx = self._buf.find(sep, self._pos)
            if idx < 0:
                # the last len(sep)-1 bytes may be the start of the delimiter - keep them
                flush(len(self._buf) - len(sep) + 1)
                if not self._fill(self.available() + _CHUNK):
                    return None
                continue

            tail_at = idx + len(sep)
            self._fill(tail_at - self._pos + _TAIL_LOOKAHEAD)
            final, used = _classify_tail(bytes(self._buf[tail_at:tail_at + _TAIL_LOOKAHEAD]))
            if final is None:
                flush(idx + 1)                  # random sequence inside the data
                continue

            flush(idx)                          # after the flush _pos == idx
            self._pos += len(sep) + used
            self._compact()
            return total, final

    def read_until(self, sep: bytes, cap: Optional[int] = None) -> Optional[bytes]:
        """Return the data before the delimiter and consume the delimiter; None if it never arrives."""
        search = self._pos
        while True:
            idx = self._buf.find(sep, search)
            if idx >= 0:
                if cap is not None and idx - self._pos > cap:
                    raise MultipartError("request part exceeds the allowed limit")
                data = bytes(self._buf[self._pos:idx])
                self._pos = idx + len(sep)
                self._compact()
                return data
            if cap is not None and self.available() > cap:
                raise MultipartError("request part exceeds the allowed limit")
            # the delimiter may be split between two reads - overlap len(sep)-1
            search = max(self._pos, len(self._buf) - len(sep) + 1)
            if not self._fill(self.available() + _CHUNK):
                return None


# --- header helpers --------------------------------------------------

def _unquote(raw: str) -> str:
    """Strip quotes and backslashes from a header parameter value."""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        out, esc = [], False
        for ch in raw[1:-1]:
            if esc:
                out.append(ch)
                esc = False
            elif ch == "\\":
                esc = True
            else:
                out.append(ch)
        if esc:
            out.append("\\")
        return "".join(out)
    return raw


def _split_params(value: str):
    """Split a header into the main value and a parameter dict, respecting quotes."""
    chunks, cur, quoted, esc = [], [], False, False
    for ch in value:
        if esc:
            cur.append(ch)
            esc = False
            continue
        if quoted and ch == "\\":
            cur.append(ch)
            esc = True
            continue
        if ch == '"':
            quoted = not quoted
            cur.append(ch)
            continue
        if ch == ";" and not quoted:
            chunks.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    chunks.append("".join(cur))

    params: Dict[str, str] = {}
    for item in chunks[1:]:
        key, sep, raw = item.partition("=")
        key = key.strip().lower()
        if not key:
            continue
        # the first occurrence wins - a repeated parameter must not override it
        params.setdefault(key, _unquote(raw) if sep else "")
    return chunks[0].strip(), params


def _ext_filename(raw: str) -> Optional[str]:
    """Decode the RFC 5987 notation  filename*=UTF-8''%C5%BElut.png ."""
    charset, _, rest = raw.partition("'")
    _lang, _, encoded = rest.partition("'")
    if not encoded:
        return None
    try:
        return unquote_to_bytes(encoded).decode(charset or "utf-8", "replace")
    except LookupError:
        return None


def _decode_headers(blob: bytes) -> str:
    """Convert the part headers to text - browsers send file names in UTF-8."""
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError:
        return blob.decode("latin-1")


def _parse_part_headers(blob: bytes) -> Dict[str, str]:
    """Parse the headers of one part, including continuation lines."""
    lines: List[str] = []
    for line in _LINE_RE.split(_decode_headers(blob)):
        if not line:
            continue
        if line[0] in " \t" and lines:
            lines[-1] += line            # folded header (RFC 5322 folding)
        else:
            lines.append(line)

    headers: Dict[str, str] = {}
    for line in lines:
        name, sep, val = line.partition(":")
        if not sep or not name.strip():
            raise MultipartError("part has a malformed header")
        headers.setdefault(name.strip().lower(), val.strip())
    return headers


def _boundary_from(content_type: str) -> bytes:
    """Extract the boundary from Content-Type (including the quoted variant)."""
    if not content_type:
        raise MultipartError("missing Content-Type header")
    main, params = _split_params(content_type)
    if not main.lower().startswith("multipart/"):
        raise MultipartError("expected type multipart/form-data")
    raw = params.get("boundary", "")
    if not raw:
        raise MultipartError("boundary missing in Content-Type")
    try:
        boundary = raw.encode("ascii")
    except UnicodeEncodeError:
        raise MultipartError("boundary contains disallowed characters") from None
    if not _BOUNDARY_RE.match(boundary) or boundary.endswith(b" "):
        raise MultipartError("boundary contains disallowed characters")
    return boundary


def _body_limit(content_length, max_bytes) -> int:
    """Check the body length against the cap and return how many bytes may be read."""
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise MultipartError("invalid max_bytes limit")
    try:
        length = int(content_length)
    except (TypeError, ValueError):
        raise MultipartError("invalid Content-Length header") from None
    if length <= 0:
        raise MultipartError("empty request body")
    if length > max_bytes:
        raise MultipartError("request body is too large")
    return length


# --- the parser itself ---------------------------------------------------------

def parse_multipart(fp, content_type, content_length, max_bytes, *,
                    max_parts: int = MAX_PARTS,
                    max_header_bytes: int = MAX_HEADER_BYTES,
                    max_file_bytes: Optional[int] = None,
                    max_field_bytes: int = MAX_FIELD_BYTES,
                    spool_dir: Optional[str] = None) -> List[Part]:
    """Read a multipart/form-data body from fp and return the parts in input order.

    max_file_bytes (optional) is a cap on the content of one file part; when
    exceeded, reading is aborted with FileTooLargeError right during reading.
    max_field_bytes is a cap on a plain form field (held in memory).
    spool_dir: files are streamed into temporary files in this directory
    (Part.path); otherwise they are held in memory. On any error the temporary
    files already created are deleted; on success the caller deletes them
    (Part.discard()).
    """
    parts: List[Part] = []
    try:
        return _parse(fp, content_type, content_length, max_bytes, max_parts,
                      max_header_bytes, max_file_bytes, max_field_bytes,
                      spool_dir, parts)
    except BaseException:
        for p in parts:
            p.discard()
        raise


def _parse(fp, content_type, content_length, max_bytes, max_parts,
           max_header_bytes, max_file_bytes, max_field_bytes, spool_dir, parts):
    boundary = _boundary_from(content_type)
    limit = _body_limit(content_length, max_bytes)
    sep = b"\r\n--" + boundary          # delimiter per RFC 7578

    reader = _Reader(fp, limit)
    # The first delimiter need not be preceded by CRLF - we add it artificially,
    # after which it is enough to keep searching for one and the same sequence.
    reader.prime(b"\r\n")
    opening = reader.read_to_delimiter(sep, MAX_PREAMBLE_BYTES)
    if opening is None:
        raise MultipartError("opening delimiter missing in body (boundary mismatch?)")

    _preamble, final = opening
    while not final:
        # check the cap before we start reading more content
        if len(parts) >= max_parts:
            raise MultipartError(f"body has too many parts (max {max_parts})")
        # part headers end with an empty line; a part without headers starts straight with CRLF
        if reader.peek(2) == b"\r\n":
            reader.skip(2)
            raw_head = b""
        else:
            raw_head = reader.read_until(b"\r\n\r\n", max_header_bytes)
            if raw_head is None:
                raise MultipartError("part headers are not terminated")
        headers = _parse_part_headers(raw_head)

        is_file = _has_filename(headers)
        if is_file and spool_dir is not None:
            spool = _Spool(spool_dir)
            try:
                rest = reader.stream_to_delimiter(
                    sep, spool.write, max_file_bytes, FileTooLargeError)
                if rest is None:
                    raise MultipartError(
                        "body ended in the middle of a part (truncated upload?)")
                spool.close()
                part = _make_part(headers, b"")
            except BaseException:
                spool.remove()
                raise
            part.path, part.size = spool.path, spool.size
            if spool.path is None:               # empty file: nothing on disk
                part.path = None
            parts.append(part)
            final = rest[1]
            continue

        rest = reader.read_to_delimiter(
            sep, max_file_bytes if is_file else max_field_bytes,
            FileTooLargeError if is_file else MultipartError)
        if rest is None:
            raise MultipartError("body ended in the middle of a part (truncated upload?)")
        content, final = rest

        parts.append(_make_part(headers, content))

    # Nobody cares about the epilogue, but it must be read to the end - otherwise
    # on a keep-alive connection the rest of the body would look like the start
    # of the next request.
    reader.drain()
    return parts


def _has_filename(headers: Dict[str, str]) -> bool:
    """Quick preliminary check from the headers: is the part a file? (raises no errors)"""
    _value, params = _split_params(headers.get("content-disposition", ""))
    return "filename" in params or "filename*" in params


def _make_part(headers: Dict[str, str], content: bytes) -> Part:
    """Build a Part from the part headers and its content."""
    disposition = headers.get("content-disposition", "")
    if not disposition:
        raise MultipartError("part has no Content-Disposition header")
    _value, params = _split_params(disposition)
    if "name" not in params:
        raise MultipartError("part has no field name")

    filename = None
    if "filename*" in params:
        filename = _ext_filename(params["filename*"])
    if filename is None and "filename" in params:
        filename = params["filename"]
    return Part(name=params["name"], filename=filename, content=content,
                content_type=headers.get("content-type", ""))
