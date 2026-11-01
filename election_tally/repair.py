"""Extracts a TSE text file from its zip, fixing the defects that would stop the CSV parser."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

BLOCK_SIZE = 1 << 24
_SEPARATOR = b'";"'
_C1_BYTES = bytes(range(0x80, 0xA0))
# UTF-8 of the Latin-1 capitals, such as Ã. Only 0xC3 maps back to one byte below 0xA0 that Latin-1
# can hold; any other pair is text in another encoding (a Windows-1252 `É“`), left as it came.
_UTF8_IN_LATIN1 = re.compile(rb"\xc3[\x80-\x9f]")


def _repair_line(line: bytes) -> bytes:
    """Escapes the inner quotes of the fields of a line in which every field is quoted."""
    body = line.rstrip(b"\r\n")
    separators = body.count(_SEPARATOR)
    if body[:1] != b'"' or body[-1:] != b'"' or not separators or body.count(b";") != separators:
        return line
    fields = [f.replace(b'""', b'"').replace(b'"', b'""') for f in body[1:-1].split(_SEPARATOR)]
    return b'"' + _SEPARATOR.join(fields) + b'"' + line[len(body):]


def repair_chunk(chunk: bytes) -> bytes:
    """Fixes the TSE's defects in whole lines of a Latin-1 file; both would stop the parser.

    Unescaped quotes in names (2018, 2020), and UTF-8 text such as IRMÃO (2016), whose bytes
    0x80-0x9F DuckDB refuses as Latin-1. Clean chunks cost two counts, not a pass per line.
    """
    if len(chunk.translate(None, _C1_BYTES)) != len(chunk):
        chunk = _UTF8_IN_LATIN1.sub(lambda m: m[0].decode().encode("latin-1"), chunk)
    if chunk.count(b'"') == 2 * (chunk.count(_SEPARATOR) + chunk.count(b"\n")):
        return chunk
    first = chunk[:chunk.find(b"\n") + 1]
    if first.count(b";") != first.count(_SEPARATOR):  # layout with unquoted numbers (2024 on)
        return chunk
    return b"".join(_repair_line(line) for line in chunk.splitlines(keepends=True))


def extract_repaired(z: zipfile.ZipFile, member: str, target: Path) -> Path:
    with z.open(member) as source, open(target, "wb") as out:
        rest = b""
        while block := source.read(BLOCK_SIZE):
            cut = block.rfind(b"\n") + 1
            if cut:
                out.write(repair_chunk(rest + block[:cut]))
                rest = b""
            rest += block[cut:]
        out.write(repair_chunk(rest))
    return target
