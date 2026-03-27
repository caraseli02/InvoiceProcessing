"""App-scoped extraction job state for hybrid sync/async extraction."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import threading
from pathlib import Path
from typing import Any, Literal

import pdfplumber

JobStatus = Literal["queued", "processing", "succeeded", "failed"]

_JOB_ID_COUNTER = 0
_JOB_ID_LOCK = threading.Lock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _next_job_id() -> str:
    global _JOB_ID_COUNTER
    with _JOB_ID_LOCK:
        _JOB_ID_COUNTER += 1
        return f"ext_{_JOB_ID_COUNTER}"


@dataclass(frozen=True)
class ExtractionJobRecord:
    """Stored extraction job state."""

    job_id: str
    owner_id: str
    dedupe_key: str
    filename: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    result_payload: dict[str, Any] | None = None
    error_payload: dict[str, str] | None = None


@dataclass(frozen=True)
class ExtractionRoutingDecision:
    """Cheap routing decision derived before full extraction."""

    should_route_async: bool
    page_count: int
    file_size_bytes: int
    routing_reason: str | None = None


class InMemoryExtractionJobStore:
    """Thread-safe app-scoped extraction job registry."""

    def __init__(self, *, ttl_sec: int) -> None:
        self._ttl_sec = ttl_sec
        self._lock = threading.Lock()
        self._jobs: dict[str, ExtractionJobRecord] = {}
        self._job_ids_by_dedupe: dict[str, str] = {}

    def create_or_get(
        self,
        *,
        owner_id: str,
        dedupe_key: str,
        filename: str,
    ) -> tuple[ExtractionJobRecord, bool]:
        """Create a queued job or return the canonical existing job."""
        with self._lock:
            self._prune_expired_locked(now=_utcnow())
            scoped_key = self._scoped_key(owner_id=owner_id, dedupe_key=dedupe_key)
            existing_job_id = self._job_ids_by_dedupe.get(scoped_key)
            if existing_job_id is not None:
                existing = self._jobs.get(existing_job_id)
                if existing is not None:
                    return existing, False

            now = _utcnow()
            record = ExtractionJobRecord(
                job_id=_next_job_id(),
                owner_id=owner_id,
                dedupe_key=dedupe_key,
                filename=filename,
                status="queued",
                created_at=now,
                updated_at=now,
                expires_at=now + timedelta(seconds=self._ttl_sec),
            )
            self._jobs[record.job_id] = record
            self._job_ids_by_dedupe[scoped_key] = record.job_id
            return record, True

    def mark_processing(self, *, job_id: str) -> None:
        with self._lock:
            record = self._jobs[job_id]
            self._jobs[job_id] = replace(
                record,
                status="processing",
                updated_at=_utcnow(),
            )

    def mark_succeeded(self, *, job_id: str, result_payload: dict[str, Any]) -> None:
        with self._lock:
            record = self._jobs[job_id]
            now = _utcnow()
            self._jobs[job_id] = replace(
                record,
                status="succeeded",
                updated_at=now,
                expires_at=now + timedelta(seconds=self._ttl_sec),
                result_payload=result_payload,
                error_payload=None,
            )

    def mark_failed(self, *, job_id: str, error_payload: dict[str, str]) -> None:
        with self._lock:
            record = self._jobs[job_id]
            now = _utcnow()
            self._jobs[job_id] = replace(
                record,
                status="failed",
                updated_at=now,
                expires_at=now + timedelta(seconds=self._ttl_sec),
                result_payload=None,
                error_payload=error_payload,
            )

    def get_for_owner(
        self,
        *,
        job_id: str,
        owner_id: str,
    ) -> ExtractionJobRecord | None:
        with self._lock:
            self._prune_expired_locked(now=_utcnow())
            record = self._jobs.get(job_id)
            if record is None:
                return None
            if record.owner_id != owner_id:
                return None
            return record

    @staticmethod
    def _scoped_key(*, owner_id: str, dedupe_key: str) -> str:
        return f"{owner_id}:{dedupe_key}"

    def _prune_expired_locked(self, *, now: datetime) -> None:
        expired_job_ids = [
            job_id
            for job_id, record in self._jobs.items()
            if record.expires_at is not None and record.expires_at <= now
        ]
        for job_id in expired_job_ids:
            record = self._jobs.pop(job_id, None)
            if record is None:
                continue
            scoped_key = self._scoped_key(
                owner_id=record.owner_id,
                dedupe_key=record.dedupe_key,
            )
            self._job_ids_by_dedupe.pop(scoped_key, None)


def inspect_extract_routing(
    *,
    pdf_path: Path,
    file_size_bytes: int,
    page_threshold: int,
    file_size_threshold: int,
) -> ExtractionRoutingDecision:
    """Choose sync or async using cheap PDF-observed signals."""
    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)

    if page_count >= page_threshold:
        return ExtractionRoutingDecision(
            should_route_async=True,
            page_count=page_count,
            file_size_bytes=file_size_bytes,
            routing_reason="page_count_threshold",
        )

    if file_size_bytes >= file_size_threshold:
        return ExtractionRoutingDecision(
            should_route_async=True,
            page_count=page_count,
            file_size_bytes=file_size_bytes,
            routing_reason="file_size_threshold",
        )

    return ExtractionRoutingDecision(
        should_route_async=False,
        page_count=page_count,
        file_size_bytes=file_size_bytes,
        routing_reason=None,
    )
