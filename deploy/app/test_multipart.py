import io
import unittest
import warnings

from multipart import FileTooLargeError, MultipartError, parse_multipart

BOUND = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
CTYPE = "multipart/form-data; boundary=" + BOUND
BIG = 10 * 1024 * 1024          # usual cap in tests: 10 MB


# --- helpers for building bodies --------------------------------------------

def field(name, value):
    """Plain form field (no file)."""
    head = 'Content-Disposition: form-data; name="%s"\r\n\r\n' % name
    return head.encode("utf-8") + value + b"\r\n"


def file_part(name, filename, data, ctype="application/octet-stream"):
    """File part, as browsers send it."""
    head = ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
            'Content-Type: %s\r\n\r\n' % (name, filename, ctype))
    return head.encode("utf-8") + data + b"\r\n"


def build(chunks, boundary=BOUND, final=True, preamble=b"", epilogue=b""):
    """Assemble the whole body from ready-made parts."""
    mark = b"--" + boundary.encode("ascii")
    body = preamble
    for chunk in chunks:
        body += mark + b"\r\n" + chunk
    if final:
        body += mark + b"--\r\n" + epilogue
    return body


def run(body, ctype=CTYPE, clen=None, max_bytes=BIG, **kw):
    """Call the parser on a ready-made in-memory body."""
    if clen is None:
        clen = len(body)
    return parse_multipart(io.BytesIO(body), ctype, clen, max_bytes, **kw)


def triples(parts):
    """Convert the result to (name, filename, content) for easy comparison."""
    return [(p.name, p.filename, p.content) for p in parts]


class CountingReader(io.BytesIO):
    """BytesIO that counts how many bytes have been read from it."""

    def __init__(self, data):
        super().__init__(data)
        self.read_bytes = 0

    def read(self, size=-1):
        chunk = super().read(size)
        self.read_bytes += len(chunk)
        return chunk


# --- ordinary cases ---------------------------------------------------------

class ParseTest(unittest.TestCase):
    def test_single_file(self):
        body = build([file_part("file", "logo.png", b"\x89PNG\r\n\x1a\n data", "image/png")])
        parts = run(body)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].name, "file")
        self.assertEqual(parts[0].filename, "logo.png")
        self.assertEqual(parts[0].content, b"\x89PNG\r\n\x1a\n data")
        self.assertEqual(parts[0].content_type, "image/png")
        self.assertTrue(parts[0].is_file)

    def test_multiple_files_at_once(self):
        body = build([file_part("file", "a.png", b"AAA"),
                      file_part("file", "b.mp4", b"BBBB"),
                      file_part("file", "c.jpg", b"C")])
        self.assertEqual(triples(run(body)),
                         [("file", "a.png", b"AAA"),
                          ("file", "b.mp4", b"BBBB"),
                          ("file", "c.jpg", b"C")])

    def test_field_without_file(self):
        body = build([field("device", b"kavarna"),
                      file_part("file", "a.png", b"AAA")])
        parts = run(body)
        self.assertEqual(parts[0].name, "device")
        self.assertIsNone(parts[0].filename)
        self.assertFalse(parts[0].is_file)
        self.assertEqual(parts[0].text(), "kavarna")
        self.assertEqual(parts[1].filename, "a.png")

    def test_empty_file(self):
        body = build([file_part("file", "prazdny.png", b"")])
        parts = run(body)
        self.assertEqual(parts[0].filename, "prazdny.png")
        self.assertEqual(parts[0].content, b"")

    def test_binary_content_with_fake_boundary(self):
        # the data contains a boundary without the leading CRLF, a boundary with a
        # tail glued on and a near-final delimiter - none of it may end the part
        data = (bytes(range(256)) * 4
                + b"--" + BOUND.encode() + b"neni-oddelovac"
                + b"\r\n--" + BOUND[:-3].encode() + b"\r\n"
                + b"\r\n--" + BOUND.encode() + b"x--\r\n"
                + bytes(range(255, -1, -1)))
        body = build([file_part("file", "bin.mp4", data), field("device", b"bar")])
        parts = run(body)
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0].content, data)
        self.assertEqual(parts[1].content, b"bar")

    def test_content_with_crlf_stays_untouched(self):
        data = b"radek1\r\nradek2\r\n\r\n--neco\r\n"
        body = build([file_part("file", "a.txt", data)])
        self.assertEqual(run(body)[0].content, data)

    def test_quoted_boundary(self):
        body = build([file_part("file", "a.png", b"AAA")])
        parts = run(body, ctype='multipart/form-data; boundary="%s"' % BOUND)
        self.assertEqual(triples(parts), [("file", "a.png", b"AAA")])

    def test_preamble_and_epilogue_are_ignored(self):
        body = build([file_part("file", "a.png", b"AAA")],
                     preamble=b"tohle je preambule\r\n",
                     epilogue=b"tohle je epilog\r\n")
        self.assertEqual(triples(run(body)), [("file", "a.png", b"AAA")])

    def test_filename_with_diacritics(self):
        body = build([file_part("file", "žluťoučký kůň.png", b"AAA")])
        self.assertEqual(run(body)[0].filename, "žluťoučký kůň.png")

    def test_filename_in_rfc5987(self):
        head = ('Content-Disposition: form-data; name="file"; '
                "filename*=UTF-8''%C5%BElut%C3%BD.png\r\n\r\n").encode("ascii")
        body = build([head + b"AAA\r\n"])
        self.assertEqual(run(body)[0].filename, "žlutý.png")

    def test_quotes_and_semicolons_in_name(self):
        head = (b'Content-Disposition: form-data; name="file"; '
                b'filename="a\\"b; c.png"\r\n\r\n')
        body = build([head + b"AAA\r\n"])
        parts = run(body)
        self.assertEqual(parts[0].filename, 'a"b; c.png')
        self.assertEqual(parts[0].content, b"AAA")

    def test_part_headers_case_insensitive(self):
        head = (b'content-disposition: FORM-DATA; NAME="file"; FileName="a.png"\r\n'
                b'CONTENT-TYPE: image/png\r\n\r\n')
        parts = run(build([head + b"AAA\r\n"]))
        self.assertEqual(triples(parts), [("file", "a.png", b"AAA")])


class TrickleReader(io.BytesIO):
    """Returns data in small chunks, like a real socket rather than a file."""

    def __init__(self, data, step=7):
        super().__init__(data)
        self.step = step

    def read(self, size=-1):
        if size is None or size < 0:
            size = self.step
        return super().read(min(size, self.step))


class StreamTest(unittest.TestCase):
    """The parser reads in a stream - the delimiter can be split between two reads."""

    def test_delimiter_split_across_reads(self):
        body = build([file_part("file", "a.bin", bytes(range(256)) * 40),
                      field("device", b"bar"),
                      file_part("file", "b.bin", b"B" * 999)])
        parts = parse_multipart(TrickleReader(body), CTYPE, len(body), BIG)
        self.assertEqual(triples(parts), triples(run(body)))
        self.assertEqual(parts[0].content, bytes(range(256)) * 40)

    def test_file_spanning_multiple_buffers(self):
        data = bytes(range(256)) * 10_000          # 2.5 MB, i.e. across buffer compaction as well
        body = build([file_part("file", "velky.mp4", data), field("device", b"bar")])
        parts = run(body)
        self.assertEqual(parts[0].content, data)
        self.assertEqual(parts[1].content, b"bar")


# --- malformed input and limits ---------------------------------------------

class ErrorTest(unittest.TestCase):
    def test_missing_boundary(self):
        body = build([file_part("file", "a.png", b"AAA")])
        with self.assertRaises(MultipartError):
            run(body, ctype="multipart/form-data")

    def test_empty_content_type(self):
        with self.assertRaises(MultipartError):
            run(build([file_part("file", "a.png", b"AAA")]), ctype="")

    def test_not_multipart(self):
        with self.assertRaises(MultipartError):
            run(build([file_part("file", "a.png", b"AAA")]),
                ctype="application/x-www-form-urlencoded")

    def test_wrong_boundary(self):
        body = build([file_part("file", "a.png", b"AAA")])
        with self.assertRaises(MultipartError):
            run(body, ctype="multipart/form-data; boundary=uplne-jiny")

    def test_boundary_with_illegal_chars(self):
        with self.assertRaises(MultipartError):
            run(b"x", ctype="multipart/form-data; boundary=\"ab\\\"cd\x01\"")

    def test_truncated_body(self):
        body = build([file_part("file", "a.png", b"A" * 100)])
        with self.assertRaises(MultipartError):
            run(body[:60], clen=len(body[:60]))

    def test_missing_final_delimiter(self):
        body = build([file_part("file", "a.png", b"AAA")], final=False)
        with self.assertRaises(MultipartError):
            run(body)

    def test_truncated_part_headers(self):
        body = b"--" + BOUND.encode() + b"\r\nContent-Disposition: form-data"
        with self.assertRaises(MultipartError):
            run(body)

    def test_part_without_content_disposition(self):
        body = build([b"Content-Type: image/png\r\n\r\nAAA\r\n"])
        with self.assertRaises(MultipartError):
            run(body)

    def test_part_without_field_name(self):
        body = build([b'Content-Disposition: form-data; filename="a.png"\r\n\r\nAAA\r\n'])
        with self.assertRaises(MultipartError):
            run(body)

    def test_empty_body(self):
        with self.assertRaises(MultipartError):
            run(b"", clen=0)

    def test_nonsense_content_length(self):
        body = build([file_part("file", "a.png", b"AAA")])
        with self.assertRaises(MultipartError):
            run(body, clen="nesmysl")

    def test_max_bytes_exceeded(self):
        body = build([file_part("file", "velky.png", b"A" * 5000)])
        with self.assertRaises(MultipartError):
            run(body, max_bytes=1024)

    def test_never_reads_more_than_max_bytes(self):
        # the client lies about the length and keeps sending - the parser may read at most max_bytes
        body = build([file_part("file", "a.png", b"A" * 100_000)])
        fp = CountingReader(body + b"A" * 500_000)
        with self.assertRaises(MultipartError):
            parse_multipart(fp, CTYPE, 4096, 4096)
        self.assertLessEqual(fp.read_bytes, 4096)

    def test_part_count_cap(self):
        body = build([field("f%d" % i, b"x") for i in range(10)])
        self.assertEqual(len(run(body, max_parts=10)), 10)
        with self.assertRaises(MultipartError):
            run(body, max_parts=5)

    def test_header_length_cap(self):
        head = ('Content-Disposition: form-data; name="file"; filename="a.png"\r\n'
                'X-Vata: %s\r\n\r\n' % ("x" * 5000)).encode("ascii")
        body = build([head + b"AAA\r\n"])
        self.assertEqual(run(body)[0].filename, "a.png")
        with self.assertRaises(MultipartError):
            run(body, max_header_bytes=512)

    def test_error_is_always_multiparterror(self):
        self.assertTrue(issubclass(MultipartError, Exception))
        for bad in (b"", b"nesmysl", b"--", b"\r\n\r\n", b"--" + BOUND.encode()):
            with self.subTest(body=bad):
                with self.assertRaises(MultipartError):
                    run(bad, clen=max(len(bad), 1))


# --- parity with the original cgi.FieldStorage ------------------------------

def cgi_triples(body, ctype):
    """Parse the body with the old cgi.FieldStorage and return (name, filename, content)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import cgi
        fs = cgi.FieldStorage(
            fp=io.BytesIO(body),
            headers={"content-type": ctype, "content-length": str(len(body))},
            environ={"REQUEST_METHOD": "POST"})
    out = []
    for f in fs.list or []:
        raw = f.value if isinstance(f.value, bytes) else f.value.encode("utf-8")
        out.append((f.name, f.filename, raw))
    return out


try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import cgi as _cgi
except ImportError:                      # Python 3.13+ no longer has the cgi module
    _cgi = None


@unittest.skipUnless(_cgi, "the cgi module is gone in this Python")
class CgiParityTest(unittest.TestCase):
    """On realistic bodies the new parser must return exactly what cgi.FieldStorage does."""

    def assert_parity(self, body, ctype=CTYPE):
        self.assertEqual(triples(run(body, ctype=ctype)), cgi_triples(body, ctype))

    def test_parity_single_file(self):
        body = build([field("device", b"kavarna"),
                      file_part("file", "logo.png", b"\x89PNG\r\n\x1a\n" + bytes(range(64)),
                               "image/png")])
        self.assert_parity(body)

    def test_parity_multiple_files(self):
        body = build([file_part("file", "a.png", b"\x89PNG" + b"\x00" * 300, "image/png"),
                      file_part("file", "b.mp4", bytes(range(256)) * 8, "video/mp4"),
                      file_part("file", "prazdny.jpg", b"", "image/jpeg"),
                      field("transition", b"fade")])
        self.assert_parity(body)

    def test_parity_binary_content_and_diacritics(self):
        data = (bytes(range(256)) * 3 + b"--" + BOUND.encode() + b"-uvnitr\r\n"
                + b"radek\r\nradek\r\n")
        body = build([file_part("file", "žluťoučký kůň.mp4", data, "video/mp4"),
                      field("device", "kavárna".encode("utf-8"))])
        self.assert_parity(body)

    def test_parity_quoted_boundary(self):
        body = build([file_part("file", "a.png", b"AAA", "image/png")])
        self.assert_parity(body, ctype='multipart/form-data; boundary="%s"' % BOUND)


class FileCapTest(unittest.TestCase):
    """The per-file cap is enforced while reading, not after the whole body is loaded."""

    def test_huge_file_is_rejected_before_being_read(self):
        body = build([file_part("f0", "a.mp4", b"\x00" * (8 * 1024 * 1024))])
        fp = CountingReader(body)
        with self.assertRaises(FileTooLargeError):
            parse_multipart(fp, CTYPE, len(body), 20 * 1024 * 1024,
                            max_file_bytes=1024 * 1024)
        self.assertLess(fp.read_bytes, 2 * 1024 * 1024)

    def test_error_is_subclass_of_multiparterror(self):
        self.assertTrue(issubclass(FileTooLargeError, MultipartError))

    def test_file_exactly_at_limit_passes(self):
        body = build([file_part("f0", "a.png", b"x" * 1000)])
        parts = run(body, max_file_bytes=1000)
        self.assertEqual(len(parts[0].content), 1000)

    def test_one_byte_over_fails(self):
        body = build([file_part("f0", "a.png", b"x" * 1001)])
        with self.assertRaises(FileTooLargeError):
            run(body, max_file_bytes=1000)

    def test_second_file_over_limit(self):
        body = build([file_part("f0", "a.png", b"x" * 10),
                      file_part("f1", "b.png", b"x" * 5000)])
        with self.assertRaises(FileTooLargeError):
            run(body, max_file_bytes=1000)

    def test_plain_field_not_limited_by_file_cap(self):
        body = build([field("device", b"d" * 5000), file_part("f0", "a.png", b"x" * 10)])
        parts = run(body, max_file_bytes=1000)
        self.assertEqual(len(parts[0].content), 5000)

    def test_without_max_file_bytes_unchanged(self):
        body = build([file_part("f0", "a.png", b"x" * 5000)])
        self.assertEqual(len(run(body)[0].content), 5000)

    def test_stream_in_small_chunks(self):
        body = build([file_part("f0", "a.png", b"x" * 5000)])
        with self.assertRaises(FileTooLargeError):
            parse_multipart(TrickleReader(body), CTYPE, len(body), BIG,
                            max_file_bytes=1000)


if __name__ == "__main__":
    unittest.main()
