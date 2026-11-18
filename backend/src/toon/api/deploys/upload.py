"""The multipart upload, streamed into a spool file against the body limit."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from fastapi import Request
from python_multipart import MultipartParser
from python_multipart.exceptions import FormParserError
from python_multipart.multipart import parse_options_header
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from toon.api.errors import ApiError
from toon.ingest.service import RoomGuard
from toon.ingest.store import ContentStore

# Named after the package, not this module: the deploy API logs as one.
_logger = logging.getLogger(__package__)

FILE_FIELD = "file"
PREVIEW_FIELD = "preview"
BASE_PATH_FIELD = "basePath"
# Non-file fields stay in memory: keep them small (a preview ref is a slug of
# at most 63 characters).
MAX_FIELD_BYTES = 4096
MAX_FIELDS = 16


def _decode(value: bytes) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value.decode("latin-1")


@dataclass(frozen=True)
class _Upload:
    filename: str
    spool: Path
    fields: dict[str, str]


class _MultipartSpooler:
    """Callbacks for python-multipart: the data of the `file` part is
    collected chunk by chunk in `to_write` (the loop flushes it to the spool
    file), the other fields land bounded in `fields`."""

    def __init__(self) -> None:
        self.filename: str | None = None
        self.file_complete = False
        self.end_seen = False
        self.fields: dict[str, str] = {}
        self.to_write: list[bytes] = []
        self._header_name = b""
        self._header_value = b""
        self._disposition: bytes | None = None
        self._field_name = ""
        self._is_file = False
        self._value = bytearray()
        self._field_count = 0

    def on_part_begin(self) -> None:
        self._disposition = None
        self._field_name = ""
        self._is_file = False
        self._value = bytearray()

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._header_name += data[start:end]

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._header_value += data[start:end]

    def on_header_end(self) -> None:
        if self._header_name.lower() == b"content-disposition":
            self._disposition = self._header_value
        self._header_name = b""
        self._header_value = b""

    def on_headers_finished(self) -> None:
        _, options = parse_options_header(self._disposition)
        name = options.get(b"name")
        if name is None:
            raise ApiError(422, "MULTIPART_INVALID.no_field_name")
        self._field_name = _decode(name)
        if b"filename" in options:
            if self._field_name != FILE_FIELD:
                raise ApiError(
                    422,
                    "MULTIPART_INVALID.unexpected_file_field",
                    params={"field": self._field_name},
                )
            if self.filename is not None:
                raise ApiError(422, "MULTIPART_INVALID.more_than_one_file")
            self.filename = _decode(options[b"filename"])
            self._is_file = True
            return
        self._field_count += 1
        if self._field_count > MAX_FIELDS:
            raise ApiError(422, "MULTIPART_INVALID.too_many_fields")

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self._is_file:
            self.to_write.append(data[start:end])
            return
        if len(self._value) + (end - start) > MAX_FIELD_BYTES:
            raise ApiError(
                422, "MULTIPART_INVALID.field_too_large", params={"field": self._field_name}
            )
        self._value += data[start:end]

    def on_part_end(self) -> None:
        if self._is_file:
            self.file_complete = True
        else:
            self.fields[self._field_name] = _decode(bytes(self._value))

    def on_end(self) -> None:
        self.end_seen = True


def _too_large(max_body: int) -> ApiError:
    return ApiError(413, "BODY_TOO_LARGE", params={"max_body": max_body})


def _invalid_multipart(error: Exception) -> ApiError:
    # The parser's own wording stays in the log: `detail` never carries
    # internal details (api/errors.py).
    _logger.info("multipart body refused: %s", error)
    return ApiError(422, "MULTIPART_INVALID")


async def _spool_upload(
    request: Request, store: ContentStore, max_body: int, room: RoomGuard
) -> _Upload:
    """Streams the request body into a spool file in the store's tempdir. A
    Content-Length above the limit is rejected without reading; without (or
    with a matching) Content-Length, reading stops at the first chunk that
    crosses the limit, or that `room` refuses. On any error the spool file is
    gone."""
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > max_body:
        raise _too_large(max_body)
    kind, options = parse_options_header(request.headers.get("content-type"))
    boundary = options.get(b"boundary")
    if kind != b"multipart/form-data" or not boundary:
        raise ApiError(422, "NOT_MULTIPART", params={"field": FILE_FIELD})

    spooler = _MultipartSpooler()
    try:
        parser = MultipartParser(
            boundary,
            {
                "on_part_begin": spooler.on_part_begin,
                "on_part_data": spooler.on_part_data,
                "on_part_end": spooler.on_part_end,
                "on_header_field": spooler.on_header_field,
                "on_header_value": spooler.on_header_value,
                "on_header_end": spooler.on_header_end,
                "on_headers_finished": spooler.on_headers_finished,
                "on_end": spooler.on_end,
            },
        )
    except FormParserError as error:
        raise _invalid_multipart(error) from error

    spool_path = store.new_spool_file()
    spool: IO[bytes] = await run_in_threadpool(lambda: spool_path.open("xb"))
    read_bytes_count = 0
    succeeded = False
    try:
        async for chunk in request.stream():
            read_bytes_count += len(chunk)
            if read_bytes_count > max_body:
                raise _too_large(max_body)
            try:
                parser.write(chunk)
            except FormParserError as error:
                raise _invalid_multipart(error) from error
            if spooler.to_write:
                room.reserve(sum(map(len, spooler.to_write)))
                await run_in_threadpool(spool.writelines, spooler.to_write)
                spooler.to_write = []
        parser.finalize()
        await run_in_threadpool(spool.close)
        if not spooler.filename or not spooler.file_complete:
            raise ApiError(422, "FILE_MISSING", params={"field": FILE_FIELD})
        if not spooler.end_seen:
            raise ApiError(422, "MULTIPART_INVALID.incomplete")
        succeeded = True
    except ClientDisconnect as error:
        raise ApiError(400, "CLIENT_ABORTED") from error
    finally:
        # On success the spool stays until the ingest is done with it.
        if not succeeded:
            spool.close()
            spool_path.unlink(missing_ok=True)
    return _Upload(filename=spooler.filename, spool=spool_path, fields=spooler.fields)
