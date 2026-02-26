"""Upload-related service helpers."""

import hashlib
from pathlib import Path
from typing import BinaryIO

from fastapi import HTTPException, status

UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MB


def save_upload_with_limit(
    source: BinaryIO, destination: Path, max_file_size: int
) -> tuple[int, str]:
    """Stream upload to disk while enforcing max file size."""
    source.seek(0)
    total_bytes = 0
    digest = hashlib.sha256()

    with destination.open("wb") as output_file:
        while True:
            chunk = source.read(UPLOAD_CHUNK_SIZE)
            if not chunk:
                break

            total_bytes += len(chunk)
            if total_bytes > max_file_size:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=(
                        f"File too large: {total_bytes:,} bytes "
                        f"(max {max_file_size:,} bytes)"
                    ),
                )

            output_file.write(chunk)
            digest.update(chunk)

    return total_bytes, digest.hexdigest()
