"""What a screenshot sent with feedback really is (serving/feedback_images.py):
the format by its magic bytes, the dimensions from the header alone, and
animations and oversized canvases refused before anything is decoded. The
walk to the header is bounded, so a file crafted to make it long costs no
more than the largest header Toon reads.

The fixtures marked as encoder output were written by real encoders (macOS
sips, ffmpeg, cwebp and img2webp) from a 5 x 3 pixel image, so the parser is
held to what software really writes, not only to headers made up here.
"""

from __future__ import annotations

import base64
import struct
import time
import zlib

import pytest

from toon.serving.feedback_images import (
    HEADER_WINDOW,
    JPEG,
    MAX_PIXELS,
    MAX_SEGMENTS,
    MAX_SIDE,
    PNG,
    WEBP,
    ImageInfo,
    acceptable_screenshot,
    read_image,
)

# Encoder output, 5 x 3 pixels each.
PNG_FILE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAUAAAADCAIAAADUVFKvAAAAKUlEQVR4nBXHQQHAQAgDsMpBREUgJ3KQult+SWJiQ1wkNbWlru8M"
    "63d8N/cPPeP0ExcAAAAASUVORK5CYII="
)
# macOS sips: JFIF, then EXIF and Photoshop segments before the frame header.
JPEG_SIPS = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAASABIAAD/4QBMRXhpZgAATU0AKgAAAAgAAYdpAAQAAAABAAAAGgAAAAAAA6ABAAMAAAABAAEAAKACAAQA"
    "AAABAAAABaADAAQAAAABAAAAAwAAAAD/7QA4UGhvdG9zaG9wIDMuMAA4QklNBAQAAAAAAAA4QklNBCUAAAAAABDUHYzZjwCyBOmA"
    "CZjs+EJ+/8AAEQgAAwAFAwEiAAIRAQMRAf/EAB8AAAEFAQEBAQEBAAAAAAAAAAABAgMEBQYHCAkKC//EALUQAAIBAwMCBAMFBQQE"
    "AAABfQECAwAEEQUSITFBBhNRYQcicRQygZGhCCNCscEVUtHwJDNicoIJChYXGBkaJSYnKCkqNDU2Nzg5OkNERUZHSElKU1RVVldY"
    "WVpjZGVmZ2hpanN0dXZ3eHl6g4SFhoeIiYqSk5SVlpeYmZqio6Slpqeoqaqys7S1tre4ubrCw8TFxsfIycrS09TV1tfY2drh4uPk"
    "5ebn6Onq8fLz9PX29/j5+v/EAB8BAAMBAQEBAQEBAQEAAAAAAAABAgMEBQYHCAkKC//EALURAAIBAgQEAwQHBQQEAAECdwABAgMR"
    "BAUhMQYSQVEHYXETIjKBCBRCkaGxwQkjM1LwFWJy0QoWJDThJfEXGBkaJicoKSo1Njc4OTpDREVGR0hJSlNUVVZXWFlaY2RlZmdo"
    "aWpzdHV2d3h5eoKDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uLj5OXm5+jp6vLz"
    "9PX29/j5+v/bAEMAAgICAgICAwICAwUDAwMFBgUFBQUGCAYGBgYGCAoICAgICAgKCgoKCgoKCgwMDAwMDA4ODg4ODw8PDw8PDw8P"
    "D//bAEMBAgICBAQEBwQEBxALCQsQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEP/dAAQA"
    "Af/aAAwDAQACEQMRAD8A8f8Ah98OvBH9iD/iTwdux/xrvP8AhXXgj/oDwfkf8ar/AA+/5Ag/Cu8rXN81xX1mp+9lv/Mz7Hw9zrGf"
    "2JhP30vhX2n/AJn/2Q=="
)
# ffmpeg: a comment segment right after the start of the image.
JPEG_FFMPEG = base64.b64decode(
    "/9j//gAPTGF2YzYzLjEuMTAyAP/bAEMACAoKCwoLDQ0NDQ0NEA8QEBAQEBAQEBAQEBISEhUVFRISEhAQEhIUFBUVFxcXFRUVFRcX"
    "GRkZHh4cHCMjJCsrM//EAFwAAQEAAAAAAAAAAAAAAAAAAAQFAQEBAAAAAAAAAAAAAAAAAAAEAxABAQEBAQAAAAAAAAAAAAAABDMA"
    "EwIRAAIBAgcBAAAAAAAAAAAAAAADMgQFAQISkUE0FDP/wAARCAADAAUDARIAAhIAAxIA/9oADAMBAAIRAxEAPwCOIJeUvGaKWS+o"
    "drniHfMpaap/n+mbgLaevsf/2Q=="
)
WEBP_LOSSY = base64.b64decode(
    "UklGRlIAAABXRUJQVlA4IEYAAAAQAgCdASoFAAMAAUAmJaACdEyAAdWtw84AAP73354HWbze6RTc4uBuGeTZd1bfV//luriA+fGQ"
    "/mJWjBxua/+PW/PaMAAA"
)
WEBP_LOSSLESS = base64.b64decode(
    "UklGRj4AAABXRUJQVlA4TDIAAAAvBIAAAHcwPAKBZJz8acYZvPkPDQAwjDFmAADAVCQb7z38GlmUkkUlWQSL6H9U+07cJw=="
)
# cwebp with an alpha channel: VP8X, then ALPH and VP8.
WEBP_ALPHA = base64.b64decode(
    "UklGRnYAAABXRUJQVlA4WAoAAAAQAAAABAAAAgAAQUxQSAoAAAABB1DkiAhERP8DVlA4IEYAAAAQAgCdASoFAAMAAUAmJaACdEyA"
    "AdWtw84AAP73354HWbze6RTc4uBuGeTZd1bfV//luriA+fGQ/mJWjBxua/+PW/PaMAAA"
)
# img2webp, two frames: VP8X with the animation flag, ANIM and ANMF.
WEBP_ANIMATED = base64.b64decode(
    "UklGRgYBAABXRUJQVlA4WAoAAAASAAAABAAAAgAAQU5JTQYAAAD/////AABBTk1GZgAAAAAAAAAAAAQAAAIAAGQAAAJBTFBICgAA"
    "AAEHUOSICERE/wNWUDggPAAAABACAJ0BKgUAAwACADQloAJ0TIAB1a3DzgAA/vTAv5moBxhOub+Ll9oQseje//uHrl+fltpiR8j6"
    "atkgAEFOTUZsAAAAAAAAAAAABAAAAgAAZAAAAkFMUEgKAAAAAQdQ5IgIRET/A1ZQOCBCAAAAsAEAnQEqBQADAAIANCWwAnQBDv4C"
    "7AD+7Rl/M3oOPwAP/jevs4DuMhRXlzP+b8YRvCK8/0ModxQf/NfGYbe2wAAA"
)
# ffmpeg, two frames: acTL before the first IDAT.
PNG_ANIMATED = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAUAAAADCAYAAABbNsX4AAAACXBIWXMAAAAAAAAAAQCEeRdzAAAACGFjVEwAAAACAAAAAPONk3AA"
    "AAAaZmNUTAAAAAAAAAAFAAAAAwAAAAAAAAAAAAAAAQAAKqRK8gAAABpJREFUeJxjZGCoOKHBwMCAjFkYbBgwAFZBAHoGAmJL8y8N"
    "AAAAGmZjVEwAAAABAAAAAQAAAAEAAAAAAAAAAAAAAAEAAYc+LMMAAAAPZmRBVAAAAAJ4nGNkAAIAAAoAAurgft0AAAAASUVORK5C"
    "YII="
)


def _png_chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def png_header(width: int, height: int, *, before_idat: tuple[bytes, ...] = (), idat: bool = True) -> bytes:
    """A PNG as far as the parser reads it: signature, IHDR, chunks, IDAT."""
    ihdr = _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    tail = _png_chunk(b"IDAT", zlib.compress(b"\x00")) if idat else b""
    return b"\x89PNG\r\n\x1a\n" + ihdr + b"".join(before_idat) + tail


def _jpeg_segment(marker: int, body: bytes) -> bytes:
    return bytes((0xFF, marker)) + struct.pack(">H", len(body) + 2) + body


def jpeg_header(width: int, height: int, *, frame: int = 0xC0, before: bytes = b"") -> bytes:
    sof = _jpeg_segment(frame, struct.pack(">BHHB", 8, height, width, 1) + b"\x01\x11\x00")
    return b"\xff\xd8" + before + sof + b"\xff\xda\x00\x02\xff\xd9"


def _riff_chunk(kind: bytes, body: bytes) -> bytes:
    return kind + struct.pack("<I", len(body)) + body + (b"\x00" if len(body) % 2 else b"")


def riff(*chunks: bytes, size_offset: int = 0) -> bytes:
    content = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(content) + size_offset) + content


def vp8(width: int, height: int, *, tag: bytes = b"\x10\x02\x00", code: bytes = b"\x9d\x01\x2a") -> bytes:
    return _riff_chunk(b"VP8 ", tag + code + struct.pack("<HH", width, height) + b"\x00" * 6)


def vp8l(width: int, height: int, *, signature: int = 0x2F, version: int = 0) -> bytes:
    bits = (width - 1) | ((height - 1) << 14) | (version << 29)
    return _riff_chunk(b"VP8L", bytes((signature,)) + bits.to_bytes(4, "little") + b"\x00" * 4)


def vp8x(width: int, height: int, *, flags: int = 0) -> bytes:
    body = bytes((flags, 0, 0, 0)) + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return _riff_chunk(b"VP8X", body)


class TestEncoderOutput:
    @pytest.mark.parametrize(
        ("data", "media_type"),
        [
            (PNG_FILE, PNG),
            (JPEG_SIPS, JPEG),
            (JPEG_FFMPEG, JPEG),
            (WEBP_LOSSY, WEBP),
            (WEBP_LOSSLESS, WEBP),
            (WEBP_ALPHA, WEBP),
        ],
        ids=["png", "jpeg-sips", "jpeg-ffmpeg", "webp-lossy", "webp-lossless", "webp-alpha"],
    )
    def test_a_still_image_is_read_and_kept(self, data: bytes, media_type: str) -> None:
        expected = ImageInfo(media_type, 5, 3, animated=False)
        assert read_image(data) == expected
        assert acceptable_screenshot(data) == expected

    @pytest.mark.parametrize(
        ("data", "media_type"), [(PNG_ANIMATED, PNG), (WEBP_ANIMATED, WEBP)], ids=["apng", "webp"]
    )
    def test_an_animation_is_read_and_refused(self, data: bytes, media_type: str) -> None:
        assert read_image(data) == ImageInfo(media_type, 5, 3, animated=True)
        assert acceptable_screenshot(data) is None


class TestTheMagicBytesDecide:
    @pytest.mark.parametrize(
        "data",
        [
            b"",
            b"GIF89a\x01\x00\x01\x00\x00\x00\x00;",
            b"BM" + b"\x00" * 60,
            b"<svg xmlns='http://www.w3.org/2000/svg'/>",
            b"RIFF\x04\x00\x00\x00WAVE",
            b"\xff\xd8",
            b"\x89PNG\r\n\x1a",
        ],
        ids=["empty", "gif", "bmp", "svg", "riff-wave", "jpeg-soi-only", "png-short-signature"],
    )
    def test_anything_else_is_no_image(self, data: bytes) -> None:
        assert read_image(data) is None
        assert acceptable_screenshot(data) is None

    def test_a_declared_type_counts_for_nothing(self) -> None:
        """The bytes of a PNG are a PNG, whatever the part said it was; there
        is no argument for a declared type at all."""
        assert read_image(PNG_FILE).media_type == PNG


class TestPng:
    def test_the_first_chunk_must_be_ihdr(self) -> None:
        broken = b"\x89PNG\r\n\x1a\n" + _png_chunk(b"tEXt", b"x" * 13) + _png_chunk(b"IDAT", b"")
        assert read_image(broken) is None

    def test_an_ihdr_of_another_length_is_refused(self) -> None:
        broken = b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", b"\x00" * 12) + _png_chunk(b"IDAT", b"")
        assert read_image(broken) is None

    def test_an_ihdr_cut_short_is_refused(self) -> None:
        assert read_image(png_header(5, 3)[:30]) is None

    def test_without_image_data_there_is_no_image(self) -> None:
        assert read_image(png_header(5, 3, idat=False)) is None

    def test_a_chunk_length_past_the_end_ends_the_walk(self) -> None:
        runaway = struct.pack(">I", 1_000_000) + b"tEXt"
        assert read_image(png_header(5, 3, idat=False) + runaway + b"IDAT") is None

    def test_chunks_before_the_image_data_are_skipped(self) -> None:
        data = png_header(7, 2, before_idat=(_png_chunk(b"pHYs", b"\x00" * 9), _png_chunk(b"tEXt", b"a\x00b")))
        assert read_image(data) == ImageInfo(PNG, 7, 2, animated=False)

    def test_an_actl_after_the_image_data_is_no_animation(self) -> None:
        """APNG requires acTL before the first IDAT; a decoder ignores one
        after it, and so does the parser."""
        data = png_header(5, 3) + _png_chunk(b"acTL", b"\x00" * 8)
        assert read_image(data) == ImageInfo(PNG, 5, 3, animated=False)

    def test_an_actl_before_the_image_data_is_an_animation(self) -> None:
        data = png_header(5, 3, before_idat=(_png_chunk(b"acTL", b"\x00\x00\x00\x02\x00\x00\x00\x00"),))
        assert read_image(data) == ImageInfo(PNG, 5, 3, animated=True)


class TestJpeg:
    @pytest.mark.parametrize("frame", [0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF])
    def test_every_frame_header_gives_the_size(self, frame: int) -> None:
        assert read_image(jpeg_header(640, 480, frame=frame)) == ImageInfo(JPEG, 640, 480, animated=False)

    @pytest.mark.parametrize("marker", [0xC4, 0xC8, 0xCC], ids=["DHT", "JPG", "DAC"])
    def test_the_c_markers_that_are_no_frame_are_skipped(self, marker: int) -> None:
        before = _jpeg_segment(marker, b"\x00\x01\x02")
        assert read_image(jpeg_header(10, 20, before=before)) == ImageInfo(JPEG, 10, 20, animated=False)

    def test_fill_bytes_and_standalone_markers_are_skipped(self) -> None:
        before = b"\xff\xff\xff\xd0\xff\x01" + _jpeg_segment(0xFE, b"commentaar")
        assert read_image(jpeg_header(3, 4, before=before)) == ImageInfo(JPEG, 3, 4, animated=False)

    @pytest.mark.parametrize(
        "data",
        [
            b"\xff\xd8\xff",
            b"\xff\xd8\xff\xff\xff",
            b"\xff\xd8\x00\x00",
            b"\xff\xd8\xff\x00\x00\x04\x00\x00",
            b"\xff\xd8\xff\xd8\xff\xc0",
            b"\xff\xd8\xff\xd9",
            b"\xff\xd8\xff\xda\x00\x02",
            b"\xff\xd8\xff\xe0\x00",
            b"\xff\xd8\xff\xe0\x00\x01",
            b"\xff\xd8\xff\xe0\x00\x10JFIF",
            b"\xff\xd8\xff\xc0\x00\x05\x08\x00\x01",
            b"\xff\xd8\xff\xe0\x00\x02",
        ],
        ids=[
            "only-a-marker-start",
            "only-fill-bytes",
            "no-marker-after-soi",
            "marker-zero",
            "second-soi",
            "end-of-image-first",
            "scan-before-frame",
            "length-cut-short",
            "length-below-two",
            "segment-past-the-end",
            "frame-header-too-short",
            "nothing-after-a-segment",
        ],
    )
    def test_a_header_that_does_not_reach_a_frame_is_refused(self, data: bytes) -> None:
        assert read_image(data) is None

    def test_a_frame_without_a_height_is_read_and_refused(self) -> None:
        """A height of 0 leaves it to a DNL marker later in the scan; there
        is nothing to check the size against, so it is no screenshot."""
        data = jpeg_header(10, 0)
        assert read_image(data) == ImageInfo(JPEG, 10, 0, animated=False)
        assert acceptable_screenshot(data) is None


class TestWebp:
    def test_the_three_first_chunks_give_the_size(self) -> None:
        assert read_image(riff(vp8(20, 10))) == ImageInfo(WEBP, 20, 10, animated=False)
        assert read_image(riff(vp8l(20, 10))) == ImageInfo(WEBP, 20, 10, animated=False)
        assert read_image(riff(vp8x(20, 10), vp8(20, 10))) == ImageInfo(WEBP, 20, 10, animated=False)

    def test_the_scale_bits_of_a_vp8_size_are_no_part_of_it(self) -> None:
        assert read_image(riff(vp8(20 | 0x4000, 10 | 0x8000))) == ImageInfo(WEBP, 20, 10, animated=False)

    def test_a_chunk_of_odd_size_is_followed_by_its_padding(self) -> None:
        data = riff(vp8x(9, 9), _riff_chunk(b"ICCP", b"abc"), vp8(9, 9))
        assert read_image(data) == ImageInfo(WEBP, 9, 9, animated=False)

    def test_trailing_bytes_after_the_riff_data_are_ignored(self) -> None:
        assert read_image(riff(vp8(4, 4)) + b"\x00\x00") == ImageInfo(WEBP, 4, 4, animated=False)

    @pytest.mark.parametrize(
        "data",
        [
            riff(vp8(4, 4), size_offset=8),
            riff(),
            riff(b"VP8 \x00"),
            riff(b"VP8 " + struct.pack("<I", 100) + b"\x00" * 10),
            riff(_riff_chunk(b"ALPH", b"\x00\x00")),
            riff(vp8(4, 4, tag=b"\x11\x02\x00")),
            riff(vp8(4, 4, code=b"\x9d\x01\x2b")),
            riff(_riff_chunk(b"VP8 ", b"\x10\x02\x00\x9d\x01\x2a\x04")),
            riff(vp8l(4, 4, signature=0x2E)),
            riff(vp8l(4, 4, version=1)),
            riff(_riff_chunk(b"VP8L", b"\x2f\x00")),
            riff(_riff_chunk(b"VP8X", b"\x00" * 8), vp8(4, 4)),
            riff(vp8x(4, 4)),
            riff(vp8x(4, 4), _riff_chunk(b"ALPH", b"\x00\x00")),
        ],
        ids=[
            "riff-size-past-the-end",
            "no-chunk-at-all",
            "chunk-header-cut-short",
            "chunk-past-the-end",
            "unknown-first-chunk",
            "vp8-not-a-key-frame",
            "vp8-wrong-start-code",
            "vp8-cut-short",
            "vp8l-wrong-signature",
            "vp8l-unknown-version",
            "vp8l-cut-short",
            "vp8x-cut-short",
            "vp8x-without-image",
            "vp8x-with-alpha-only",
        ],
    )
    def test_a_broken_webp_is_refused(self, data: bytes) -> None:
        assert read_image(data) is None

    def test_the_animation_flag_alone_makes_an_animation(self) -> None:
        assert read_image(riff(vp8x(4, 4, flags=0x02), vp8(4, 4))) == ImageInfo(WEBP, 4, 4, animated=True)

    @pytest.mark.parametrize("chunk", [b"ANIM", b"ANMF"])
    def test_an_animation_chunk_alone_makes_an_animation(self, chunk: bytes) -> None:
        data = riff(vp8x(4, 4), _riff_chunk(chunk, b"\x00" * 6), vp8(4, 4))
        assert read_image(data) == ImageInfo(WEBP, 4, 4, animated=True)


class TestTheSizeLimits:
    def test_the_largest_side_and_canvas_pass(self) -> None:
        assert acceptable_screenshot(png_header(MAX_SIDE, 1)) == ImageInfo(PNG, MAX_SIDE, 1, animated=False)
        assert acceptable_screenshot(png_header(1, MAX_SIDE)) == ImageInfo(PNG, 1, MAX_SIDE, animated=False)
        # 4096 x 3906 is just under sixteen million pixels.
        assert MAX_SIDE * 3906 <= MAX_PIXELS
        assert acceptable_screenshot(png_header(MAX_SIDE, 3906)) is not None

    @pytest.mark.parametrize(
        ("width", "height"),
        [(MAX_SIDE + 1, 1), (1, MAX_SIDE + 1), (MAX_SIDE, 3907), (0, 1), (1, 0), (30_000, 30_000)],
        ids=["too-wide", "too-high", "too-many-pixels", "no-width", "no-height", "decompression-bomb"],
    )
    def test_anything_larger_or_empty_is_refused(self, width: int, height: int) -> None:
        """A 30000 x 30000 PNG of zeroes is a few hundred kilobytes and decodes
        to gigabytes: refused on what it declares, never decoded."""
        data = png_header(width, height)
        assert read_image(data) == ImageInfo(PNG, width, height, animated=False)
        assert acceptable_screenshot(data) is None

    def test_a_webp_canvas_is_held_to_the_same_limits(self) -> None:
        assert acceptable_screenshot(riff(vp8x(MAX_SIDE + 1, 1), vp8(1, 1))) is None
        assert acceptable_screenshot(riff(vp8l(MAX_SIDE, MAX_SIDE))) is None


# --- The header comes early ---------------------------------------------------------

# The frame header that jpeg_header writes takes 13 bytes, a segment without
# a body 4, and the signature and IHDR of a PNG 33.
JPEG_FRAME_BYTES = 13
EMPTY_SEGMENT = _jpeg_segment(0xEF, b"")
PNG_HEAD_BYTES = 33


def _jpeg_padding(size: int) -> bytes:
    """An APP15 segment of `size` bytes in all."""
    return _jpeg_segment(0xEF, b"\x00" * (size - 4))


class TestTheHeaderComesEarly:
    """The walk to the header reads at most MAX_SEGMENTS markers or chunks,
    all within the first HEADER_WINDOW bytes. A canvas writes its header in
    its first few hundred bytes, after a handful of segments."""

    def test_a_jpeg_frame_header_is_read_as_the_last_marker_the_walk_reads(self) -> None:
        before = b"\xff\xd0" * (MAX_SEGMENTS - 1)
        assert read_image(jpeg_header(8, 6, before=before)) == ImageInfo(JPEG, 8, 6, animated=False)
        assert read_image(jpeg_header(8, 6, before=before + b"\xff\xd0")) is None

    def test_a_jpeg_frame_header_ends_within_the_window(self) -> None:
        room = HEADER_WINDOW - 2 - JPEG_FRAME_BYTES
        assert read_image(jpeg_header(8, 6, before=_jpeg_padding(room))) == ImageInfo(JPEG, 8, 6, animated=False)
        assert read_image(jpeg_header(8, 6, before=_jpeg_padding(room + 1))) is None

    def test_fill_bytes_count_toward_the_window_and_not_as_markers(self) -> None:
        """However many fill bytes stand before a marker, they are one step
        of the walk, as long as the frame header still ends in the window."""
        room = HEADER_WINDOW - 2 - JPEG_FRAME_BYTES
        assert read_image(jpeg_header(8, 6, before=b"\xff" * room)) == ImageInfo(JPEG, 8, 6, animated=False)
        assert read_image(jpeg_header(8, 6, before=b"\xff" * (room + 1))) is None

    def test_idat_is_read_as_the_last_chunk_the_walk_reads(self) -> None:
        text = _png_chunk(b"tEXt", b"a\x00b")
        read = png_header(8, 6, before_idat=(text,) * (MAX_SEGMENTS - 1))
        assert read_image(read) == ImageInfo(PNG, 8, 6, animated=False)
        assert read_image(png_header(8, 6, before_idat=(text,) * MAX_SEGMENTS)) is None

    def test_the_idat_chunk_starts_within_the_window(self) -> None:
        """The walk reads the 8 bytes of a chunk's length and type, and a
        chunk takes 12 bytes more than its body."""
        body = HEADER_WINDOW - PNG_HEAD_BYTES - 12 - 8
        read = png_header(8, 6, before_idat=(_png_chunk(b"tEXt", b"\x00" * body),))
        assert read_image(read) == ImageInfo(PNG, 8, 6, animated=False)
        past = png_header(8, 6, before_idat=(_png_chunk(b"tEXt", b"\x00" * (body + 1)),))
        assert read_image(past) is None

    def test_the_webp_image_chunk_is_read_as_the_last_chunk_the_walk_reads(self) -> None:
        """VP8X comes first, then the walk through its metadata to the
        image."""
        meta = _riff_chunk(b"EXIF", b"\x00\x00")
        read = riff(vp8x(8, 6), *(meta,) * (MAX_SEGMENTS - 1), vp8(8, 6))
        assert read_image(read) == ImageInfo(WEBP, 8, 6, animated=False)
        assert read_image(riff(vp8x(8, 6), *(meta,) * MAX_SEGMENTS, vp8(8, 6))) is None

    def test_the_webp_image_chunk_starts_within_the_window(self) -> None:
        # The RIFF header takes 12 bytes, VP8X 18, a chunk 8 more than its
        # body, and the walk reads the 8 of the image chunk's header.
        body = HEADER_WINDOW - 12 - 18 - 8 - 8
        read = riff(vp8x(8, 6), _riff_chunk(b"EXIF", b"\x00" * body), vp8(8, 6))
        assert read_image(read) == ImageInfo(WEBP, 8, 6, animated=False)
        past = riff(vp8x(8, 6), _riff_chunk(b"EXIF", b"\x00" * (body + 2)), vp8(8, 6))
        assert read_image(past) is None


# The default TOON_FEEDBACK_SCREENSHOT_MAX_BYTES.
SCREENSHOT_LIMIT = 2 * 1024 * 1024


def _at_the_limit(data: bytes) -> bytes:
    """`data` followed by zero bytes, as long as a screenshot may be."""
    return data + bytes(SCREENSHOT_LIMIT - len(data))


def _packed(start: bytes, unit: bytes) -> bytes:
    """`start` and then `unit` over and over, as long as a screenshot may be."""
    return (start + unit * (SCREENSHOT_LIMIT // len(unit)))[:SCREENSHOT_LIMIT]


def _largest_jpeg() -> bytes:
    """The frame header as the last marker the walk reads, ending at the
    window's edge, and every marker before it after as many fill bytes as
    fit: the most a JPEG header Toon accepts makes the walk do."""
    markers = MAX_SEGMENTS - 1
    fill = (HEADER_WINDOW - 2 - JPEG_FRAME_BYTES) // markers - len(EMPTY_SEGMENT)
    return _at_the_limit(jpeg_header(640, 480, before=(b"\xff" * fill + EMPTY_SEGMENT) * markers))


def _largest_png() -> bytes:
    markers = MAX_SEGMENTS - 1
    body = (HEADER_WINDOW - PNG_HEAD_BYTES - 8) // markers - 12
    return _at_the_limit(png_header(640, 480, before_idat=(_png_chunk(b"tEXt", b"\x00" * body),) * markers))


def _largest_webp() -> bytes:
    markers = MAX_SEGMENTS - 1
    body = ((HEADER_WINDOW - 12 - 18 - 8) // markers - 8) & ~1
    meta = _riff_chunk(b"EXIF", b"\x00" * body)
    return _at_the_limit(riff(vp8x(640, 480), *(meta,) * markers, vp8(640, 480)))


EMPTY_PNG_CHUNK = _png_chunk(b"tEXt", b"")
EMPTY_RIFF_CHUNK = _riff_chunk(b"EXIF", b"")

# Each as long as a screenshot may be, and each against the largest header of
# its format: what it costs, and what the walk makes of it.
CRAFTED = [
    ("jpeg-restart-markers", _packed(b"\xff\xd8", b"\xff\xd0"), _largest_jpeg, None),
    ("jpeg-fill-bytes", _packed(b"\xff\xd8", b"\xff"), _largest_jpeg, None),
    ("jpeg-empty-segments", _packed(b"\xff\xd8", b"\xff\xe0\x00\x02"), _largest_jpeg, None),
    ("png-chunks-without-image-data", _packed(png_header(640, 480, idat=False), EMPTY_PNG_CHUNK), _largest_png, None),
    (
        "webp-chunks-without-an-image",
        riff(vp8x(640, 480), EMPTY_RIFF_CHUNK * ((SCREENSHOT_LIMIT - 40) // 8)),
        _largest_webp,
        None,
    ),
    (
        "webp-chunks-after-the-image",
        riff(vp8x(640, 480), vp8(640, 480), EMPTY_RIFF_CHUNK * ((SCREENSHOT_LIMIT - 60) // 8)),
        _largest_webp,
        ImageInfo(WEBP, 640, 480, animated=False),
    ),
]


def _cpu_ms(data: bytes) -> float:
    """The CPU time this thread spends on one acceptable_screenshot(data),
    the best of five runs of twenty: what other work on the machine costs
    stays out of it."""
    spent = []
    for _ in range(5):
        start = time.thread_time()
        for _ in range(20):
            acceptable_screenshot(data)
        spent.append(time.thread_time() - start)
    return min(spent) * 1000 / 20


class TestCost:
    @pytest.mark.parametrize(
        ("data", "largest", "expected"),
        [(data, largest, expected) for _, data, largest, expected in CRAFTED],
        ids=[name for name, *_ in CRAFTED],
    )
    def test_a_crafted_screenshot_costs_no_more_than_the_largest_header(self, data, largest, expected) -> None:
        """The check runs on the event loop of the one process, and a refused
        create gives its write back, so only the rate limiter bounds how often
        a client can send one. Walked to its end, a 2 MiB file of nothing but
        markers or chunks cost over 100 ms of CPU (a WebP list of every chunk
        also some 39 MiB), against a few microseconds for a real screenshot.
        Measured against the largest header Toon still reads, so the bound
        holds on a slow machine and under coverage alike."""
        header = largest()
        accepted = acceptable_screenshot(header)
        assert accepted is not None
        assert (accepted.width, accepted.height) == (640, 480)
        assert len(data) <= SCREENSHOT_LIMIT
        assert acceptable_screenshot(data) == expected
        spent, reference = _cpu_ms(data), _cpu_ms(header)
        assert spent < 2 * reference, f"{spent:.4f} ms against {reference:.4f} ms"
