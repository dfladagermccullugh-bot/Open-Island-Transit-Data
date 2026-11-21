"""Read a GTFS zip even when it arrives without its central directory.

On 2026-11-18 the Passio static GTFS for Island Transit was served five times
as a zip that stopped after the last file entry: no ``calendar.txt``, no
``calendar_dates.txt`` and no central directory. Standard zip tools refuse
such a file, but every entry that did arrive is intact and can be recovered by
walking the local file headers. This module does that and reports exactly
what was wrong, so the defect is recorded instead of hidden.
"""

from __future__ import annotations

import io
import struct
import zipfile
import zlib
from dataclasses import dataclass, field

LOCAL_HEADER = b"PK\x03\x04"
DATA_DESCRIPTOR = b"PK\x07\x08"

# GTFS requires these, plus at least one of calendar.txt / calendar_dates.txt.
REQUIRED_FILES = ("agency.txt", "stops.txt", "routes.txt", "trips.txt", "stop_times.txt")
CALENDAR_FILES = ("calendar.txt", "calendar_dates.txt")


@dataclass
class ZipContents:
    files: dict[str, bytes] = field(default_factory=dict)
    has_directory: bool = False
    problems: list[str] = field(default_factory=list)
    # Parsed text tables, filled by ``pipeline.gtfs.tables.rows``.
    tables: dict[str, list[dict[str, str]]] = field(default_factory=dict, repr=False, compare=False)

    @property
    def missing_required(self) -> list[str]:
        missing = [name for name in REQUIRED_FILES if name not in self.files]
        if not any(name in self.files for name in CALENDAR_FILES):
            missing.append("calendar.txt or calendar_dates.txt")
        return missing

    @property
    def is_valid_gtfs(self) -> bool:
        return self.has_directory and not self.problems and not self.missing_required


def read_zip(data: bytes) -> ZipContents:
    """Return every recoverable file in ``data`` plus a list of defects."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            out = ZipContents(has_directory=True)
            for info in z.infolist():
                if info.is_dir():
                    continue
                try:  # reading an entry checks its CRC, so each is read once
                    out.files[info.filename] = z.read(info)
                except (zipfile.BadZipFile, zlib.error):
                    out.problems.append(f"CRC mismatch in {info.filename}")
            return out
    except zipfile.BadZipFile as exc:
        out = _walk_local_headers(data)
        out.problems.insert(0, f"no usable central directory ({exc})")
        return out


def _walk_local_headers(data: bytes) -> ZipContents:
    out = ZipContents(has_directory=False)
    pos = 0
    while data[pos : pos + 4] == LOCAL_HEADER:
        if pos + 30 > len(data):
            out.problems.append(f"truncated local header at byte {pos}")
            break
        (_ver, flags, method, _time, _date, crc, comp_size, _size, name_len, extra_len) = (
            struct.unpack("<HHHHHIIIHH", data[pos + 4 : pos + 30])
        )
        name = data[pos + 30 : pos + 30 + name_len].decode("utf-8", errors="replace")
        start = pos + 30 + name_len + extra_len
        has_descriptor = bool(flags & 0x08)

        if method == 0:  # stored
            if has_descriptor:
                out.problems.append(f"{name}: stored entry with data descriptor, cannot size")
                break
            body = data[start : start + comp_size]
            consumed = comp_size
            complete = len(body) == comp_size
        elif method == 8:  # deflate
            inflater = zlib.decompressobj(-zlib.MAX_WBITS)
            try:
                body = inflater.decompress(data[start:])
            except zlib.error as exc:
                out.problems.append(f"{name}: corrupt deflate stream ({exc})")
                break
            complete = inflater.eof
            consumed = len(data) - start - len(inflater.unused_data)
        else:
            out.problems.append(f"{name}: unsupported compression method {method}")
            break

        if not complete:
            out.problems.append(f"{name}: entry is cut off")
            break

        pos = start + consumed
        if has_descriptor:
            if data[pos : pos + 4] == DATA_DESCRIPTOR:
                pos += 4
            if pos + 12 > len(data):
                out.problems.append(f"{name}: data descriptor is cut off")
                break
            (crc,) = struct.unpack("<I", data[pos : pos + 4])
            pos += 12
        if zlib.crc32(body) != crc:
            out.problems.append(f"{name}: CRC mismatch")
            continue
        out.files[name] = body

    if not out.files and not out.problems:
        out.problems.append("not a zip file")
    return out
