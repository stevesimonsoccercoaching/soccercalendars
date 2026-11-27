"""Controlled compressed-data corruption for XLSX test fixtures."""

import io
import struct
from zipfile import ZIP_DEFLATED, ZipFile


def corrupt_deflate_member(path, member, late=False):
    """Damage a DEFLATE block while preserving the ZIP directory."""
    buffer = io.BytesIO()

    with ZipFile(path) as source:
        with ZipFile(
            buffer, "w", ZIP_DEFLATED, compresslevel=0
        ) as target:
            for name in source.namelist():
                target.writestr(name, source.read(name))

    raw = bytearray(buffer.getvalue())

    with ZipFile(buffer) as archive:
        info = archive.getinfo(member)

    assert info.compress_type == ZIP_DEFLATED

    name_len, extra_len = struct.unpack_from(
        "<HH", raw, info.header_offset + 26
    )

    start = info.header_offset + 30 + name_len + extra_len
    offset = start

    if late:
        # The first stored block must remain readable.
        assert raw[start] & 7 == 0

        length, complement = struct.unpack_from(
            "<HH", raw, start + 1
        )

        assert length ^ complement == 0xFFFF
        assert length > 16384

        offset = start + 5 + length
        assert offset < start + info.compress_size

    # Set the reserved, invalid DEFLATE block type.
    raw[offset] = (raw[offset] & ~6) | 6

    path.write_bytes(raw)

    with ZipFile(path) as archive:
        assert member in archive.namelist()
