# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The ZIP import must not unpack more than its limits, even when an entry lies about its size."""
import io
import struct
import tracemalloc
import zipfile

import pytest

from openampere import cloud_import
from openampere.storage import Storage

REAL_SIZE = 100 * 1024 ** 2  # far above MAX_ENTRY_BYTES
DECLARED_SIZE = 64


def lying_zip() -> bytes:
    """A deflate ZIP whose single entry holds 100 MiB but declares 64 bytes in both headers."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("export/work/2026-01-01.json", b" " * REAL_SIZE)
    data = bytearray(buffer.getvalue())
    local = data.find(b"PK\x03\x04")
    central = data.find(b"PK\x01\x02")
    assert local >= 0 and central >= 0
    struct.pack_into("<I", data, local + 22, DECLARED_SIZE)
    struct.pack_into("<I", data, central + 24, DECLARED_SIZE)
    return bytes(data)


def test_entry_with_false_declared_size_is_not_inflated_in_memory(tmp_path):
    data = lying_zip()
    assert [info.file_size for info in zipfile.ZipFile(io.BytesIO(data)).infolist()] == [DECLARED_SIZE]
    storage = Storage(tmp_path / "z.db")
    tracemalloc.start()
    try:
        with pytest.raises(ValueError):
            cloud_import.import_zip(storage, data)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 ** 2, f"import_zip allocated {peak} bytes"


def test_entry_is_refused_by_the_bytes_really_unpacked():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("export/work/2026-01-01.json", b" " * 4096)
    with zipfile.ZipFile(buffer) as archive:
        assert len(cloud_import._read_entry(archive, "export/work/2026-01-01.json", 4096)) == 4096
        with pytest.raises(ValueError, match="zu groß"):
            cloud_import._read_entry(archive, "export/work/2026-01-01.json", 4095)
