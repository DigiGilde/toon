"""What a screenshot sent with site feedback really is, read from its bytes.

The type a client declares counts for nothing: the magic bytes decide between
the three raster formats Toon keeps, and the dimensions come from the header
alone (PNG from IHDR, JPEG from its frame header, WebP from VP8, VP8L or
VP8X). Nothing is decoded here. A small file can declare a canvas that
decodes to gigabytes in a reviewer's browser or in the image reader of a
Claude session, so such a file is refused on what it declares. An animation
is refused too: a screenshot is one still image.

The header has to come early: the walk to it reads at most MAX_SEGMENTS
markers or chunks, all within the first HEADER_WINDOW bytes. The check runs
on the event loop, and this way a file crafted to make the walk long costs no
more than the largest header Toon still reads, however long the file is.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass

PNG = "image/png"
JPEG = "image/jpeg"
WEBP = "image/webp"

# The widget scales its screenshots to 2000 px on the longest side, so a real
# one never comes near these.
MAX_SIDE = 4096
MAX_PIXELS = 16_000_000

# canvas.toBlob writes the frame header of a JPEG within its first few
# hundred bytes, after a handful of segments.
HEADER_WINDOW = 64 * 1024
MAX_SEGMENTS = 64

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# The first chunk of every PNG: length 13, then its type.
_PNG_IHDR = b"\x00\x00\x00\x0dIHDR"
_JPEG_SOI = b"\xff\xd8\xff"

# Start of frame: every SOFn except C4 (DHT), C8 (reserved) and CC (DAC).
_JPEG_FRAMES = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})
# Markers without a length: TEM and the restart markers.
_JPEG_STANDALONE = frozenset({0x01, *range(0xD0, 0xD8)})
# No frame header can follow these: not a marker, a second SOI, the end of
# the image, or the start of the scan data.
_JPEG_NO_FRAME = frozenset({0x00, 0xD8, 0xD9, 0xDA})
# The FF that starts a marker with the fill bytes before it, any number of
# them, skipped in one match.
_JPEG_FILL = re.compile(rb"\xff+")

_VP8_START_CODE = b"\x9d\x01\x2a"
_VP8L_SIGNATURE = 0x2F
_VP8X_ANIMATION = 0x02


@dataclass(frozen=True)
class ImageInfo:
    media_type: str
    width: int
    height: int
    animated: bool


def read_image(data: bytes) -> ImageInfo | None:
    """The format, dimensions and animation flag of a PNG, JPEG or WebP
    header, or None for anything else, a header cut short included."""
    if data.startswith(_PNG_SIGNATURE):
        return _png(data)
    if data.startswith(_JPEG_SOI):
        return _jpeg(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp(data)
    return None


def acceptable_screenshot(data: bytes) -> ImageInfo | None:
    """The screenshot's header when Toon keeps it: one of the three formats,
    still, at most MAX_SIDE on each side and MAX_PIXELS in all."""
    info = read_image(data)
    if info is None or info.animated:
        return None
    if not (0 < info.width <= MAX_SIDE and 0 < info.height <= MAX_SIDE):
        return None
    if info.width * info.height > MAX_PIXELS:
        return None
    return info


def _png(data: bytes) -> ImageInfo | None:
    if data[8:16] != _PNG_IHDR or len(data) < 33:
        return None
    width, height = struct.unpack_from(">II", data, 16)
    # IHDR is 8 + 13 + 4 bytes after the signature. An APNG announces itself
    # with acTL before the first IDAT; one after it is no animation.
    end = min(len(data), HEADER_WINDOW)
    offset = 33
    animated = False
    for _ in range(MAX_SEGMENTS):
        if offset + 8 > end:
            return None
        length, kind = struct.unpack_from(">I4s", data, offset)
        if kind == b"IDAT":
            return ImageInfo(PNG, width, height, animated)
        if kind == b"acTL":
            animated = True
        offset += 12 + length
    return None


def _jpeg(data: bytes) -> ImageInfo | None:
    end = min(len(data), HEADER_WINDOW)
    offset = 2
    for _ in range(MAX_SEGMENTS):
        fill = _JPEG_FILL.match(data, offset, end)
        if fill is None or fill.end() == end:
            return None
        marker = data[fill.end()]
        offset = fill.end() + 1
        if marker in _JPEG_STANDALONE:
            continue
        if marker in _JPEG_NO_FRAME or offset + 2 > end:
            return None
        (length,) = struct.unpack_from(">H", data, offset)
        if length < 2 or offset + length > end:
            return None
        if marker in _JPEG_FRAMES:
            if length < 7:
                return None
            height, width = struct.unpack_from(">HH", data, offset + 3)
            return ImageInfo(JPEG, width, height, False)
        offset += length
    return None


def _webp(data: bytes) -> ImageInfo | None:
    (riff_size,) = struct.unpack_from("<I", data, 4)
    end = 8 + riff_size
    first = _riff_chunk(data, 12, end) if end <= len(data) else None
    if first is None:
        return None
    kind, offset, size = first
    # The first ten bytes of a chunk hold all the parsers below read.
    head = data[offset : offset + min(size, 10)]
    if kind == b"VP8 ":
        return _with_size(_vp8(head), animated=False)
    if kind == b"VP8L":
        return _with_size(_vp8l(head), animated=False)
    if kind != b"VP8X" or size < 10:
        return None
    width = int.from_bytes(head[4:7], "little") + 1
    height = int.from_bytes(head[7:10], "little") + 1
    # After VP8X and its metadata, a still image has its VP8 or VP8L chunk,
    # and an animation its ANIM chunk and then frames.
    offset += size + (size & 1)
    for _ in range(MAX_SEGMENTS):
        chunk = _riff_chunk(data, offset, end)
        if chunk is None:
            return None
        kind, offset, size = chunk
        if kind in (b"VP8 ", b"VP8L"):
            return ImageInfo(WEBP, width, height, bool(head[0] & _VP8X_ANIMATION))
        if kind in (b"ANIM", b"ANMF"):
            return ImageInfo(WEBP, width, height, True)
        offset += size + (size & 1)
    return None


def _riff_chunk(data: bytes, offset: int, end: int) -> tuple[bytes, int, int] | None:
    """The chunk at `offset` as (fourcc, offset of its body, its size), or
    None when its header lies past the header window or the chunk runs past
    `end`, the end of the RIFF data. A chunk of odd size is followed by one
    byte of padding, which the caller steps over."""
    if offset + 8 > min(end, HEADER_WINDOW):
        return None
    kind = data[offset : offset + 4]
    (size,) = struct.unpack_from("<I", data, offset + 4)
    body = offset + 8
    if body + size > end:
        return None
    return kind, body, size


def _vp8(body: bytes) -> tuple[int, int] | None:
    # A key frame (bit 0 of the frame tag clear) starts the stream, and it
    # carries the start code and the 14-bit dimensions.
    if len(body) < 10 or body[0] & 1 or body[3:6] != _VP8_START_CODE:
        return None
    width, height = struct.unpack_from("<HH", body, 6)
    return width & 0x3FFF, height & 0x3FFF


def _vp8l(body: bytes) -> tuple[int, int] | None:
    if len(body) < 5 or body[0] != _VP8L_SIGNATURE:
        return None
    bits = int.from_bytes(body[1:5], "little")
    # The top three bits are a version number, and only 0 exists.
    if bits >> 29:
        return None
    return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1


def _with_size(size: tuple[int, int] | None, *, animated: bool) -> ImageInfo | None:
    return None if size is None else ImageInfo(WEBP, size[0], size[1], animated)


__all__ = [
    "HEADER_WINDOW",
    "JPEG",
    "MAX_PIXELS",
    "MAX_SEGMENTS",
    "MAX_SIDE",
    "PNG",
    "WEBP",
    "ImageInfo",
    "acceptable_screenshot",
    "read_image",
]
