"""Unit tests for upload service."""

from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException, status

from invproc.services.upload_service import save_upload_with_limit


def test_save_upload_with_limit_writes_file_and_hash(tmp_path: Path) -> None:
    payload = b"abc123"
    source = BytesIO(payload)
    destination = tmp_path / "upload.pdf"

    total_bytes, digest = save_upload_with_limit(source, destination, max_file_size=1024)

    assert total_bytes == len(payload)
    assert digest == "6ca13d52ca70c883e0f0bb101e425a89e8624de51db2d23925b7c0b5f5b7f5ad"
    assert destination.read_bytes() == payload


def test_save_upload_with_limit_rejects_oversized_payload(tmp_path: Path) -> None:
    source = BytesIO(b"x" * 20)
    destination = tmp_path / "upload.pdf"

    with pytest.raises(HTTPException) as exc_info:
        save_upload_with_limit(source, destination, max_file_size=10)

    assert exc_info.value.status_code == status.HTTP_413_CONTENT_TOO_LARGE
    assert "File too large" in str(exc_info.value.detail)
