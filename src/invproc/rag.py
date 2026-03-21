"""Backend-owned RAG services for catalog sync, retrieval, and evaluation."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Optional, Protocol

from openai import OpenAI

from invproc.config import InvoiceConfig
from invproc.repositories.base import (
    InvoiceImportRepository,
    ProductCatalogEmbeddingMatch,
    ProductCatalogEmbeddingRecordInput,
    ProductSyncRecord,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_catalog_embedding_text(record: ProductSyncRecord) -> str:
    """Assemble the canonical V1 catalog embedding text."""
    parts = [
        record.name.strip(),
        record.barcode.strip() if record.barcode else "",
        record.category.strip() if record.category else "",
        record.uom.strip() if record.uom else "",
    ]
    return " ".join(part for part in parts if part)


def compute_retry_delay(attempt_number: int) -> timedelta:
    """Return bounded exponential backoff for sync retries."""
    bounded_attempt = max(1, attempt_number)
    seconds = min(300, 30 * (2 ** (bounded_attempt - 1)))
    return timedelta(seconds=seconds)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(left) != len(right):
        raise ValueError("Vector dimensions must match for cosine similarity")

    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def rrf_merge(
    semantic_matches: list[ProductCatalogEmbeddingMatch],
    lexical_matches: list[ProductCatalogEmbeddingMatch],
    *,
    k: int = 60,
    top_k: int = 5,
) -> list[ProductCatalogEmbeddingMatch]:
    """Merge semantic and lexical result lists via Reciprocal Rank Fusion.

    Deduplicates by product_id, keeping the first-seen match record for metadata.
    The merged score is the sum of per-list RRF contributions.
    """
    scores: dict[str, float] = {}
    records: dict[str, ProductCatalogEmbeddingMatch] = {}

    for rank, match in enumerate(semantic_matches, start=1):
        pid = match.product_id
        scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank)
        records.setdefault(pid, match)

    for rank, match in enumerate(lexical_matches, start=1):
        pid = match.product_id
        scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank)
        records.setdefault(pid, match)

    sorted_ids = sorted(scores, key=lambda pid: scores[pid], reverse=True)[:top_k]
    return [
        ProductCatalogEmbeddingMatch(
            product_id=records[pid].product_id,
            product_snapshot_hash=records[pid].product_snapshot_hash,
            embedding_model=records[pid].embedding_model,
            embedding_text=records[pid].embedding_text,
            metadata=records[pid].metadata,
            score=scores[pid],
        )
        for pid in sorted_ids
    ]


class EmbeddingClient(Protocol):
    """Protocol for generating text embeddings."""

    def embed(self, *, model: str, text: str) -> list[float]:
        ...


class OpenAIEmbeddingClient:
    """OpenAI-backed embedding client with deterministic mock fallback."""

    def __init__(self, config: InvoiceConfig) -> None:
        self._config = config
        self._client: Optional[OpenAI] = None
        if not config.mock and config.openai_api_key:
            self._client = OpenAI(
                api_key=config.openai_api_key.get_secret_value(),
                timeout=config.openai_timeout_sec,
            )

    def embed(self, *, model: str, text: str) -> list[float]:
        if self._client is None:
            if not self._config.mock:
                raise ValueError("OpenAI embedding client not initialized (missing API key)")
            return self._mock_embed(model=model, text=text)

        response = self._client.embeddings.create(model=model, input=text)
        return list(response.data[0].embedding)

    @staticmethod
    def _mock_embed(*, model: str, text: str) -> list[float]:
        """Produce a deterministic test-friendly embedding for offline execution."""
        normalized = " ".join(text.lower().split())
        tokens = normalized.split() or ["<empty>"]
        dimensions = 16
        vector = [0.0] * dimensions
        for token in tokens:
            digest = hashlib.sha256(f"{model}:{token}".encode("utf-8")).digest()
            index = digest[0] % dimensions
            sign = 1.0 if digest[1] % 2 == 0 else -1.0
            magnitude = 1.0 + (digest[2] / 255.0)
            vector[index] += sign * magnitude
        return vector


@dataclass(frozen=True)
class CatalogSyncJobResult:
    """Result of processing a single sync row."""

    status: str
    sync_id: str | None = None
    product_id: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class CatalogRagMatch:
    """One retrieval match."""

    product_id: str
    product_snapshot_hash: str
    embedding_model: str
    score: float
    metadata: dict[str, Any]
    embedding_text: str


@dataclass(frozen=True)
class CatalogQueryResult:
    """Backend retrieval response shape."""

    query: str
    embedding_model: str
    top_k: int
    search_mode: str
    matches: list[CatalogRagMatch]

    @property
    def has_match(self) -> bool:
        return bool(self.matches)


@dataclass(frozen=True)
class CatalogEvalCase:
    """One evaluation query and expected result."""

    query: str
    expected_product_id: str


@dataclass(frozen=True)
class CatalogEvalResult:
    """Aggregate evaluation metrics."""

    total_queries: int
    top_1_hits: int
    top_5_hits: int
    cases: list[dict[str, Any]]

    @property
    def top_1_hit_rate(self) -> float:
        if self.total_queries == 0:
            return 0.0
        return self.top_1_hits / self.total_queries

    @property
    def top_5_hit_rate(self) -> float:
        if self.total_queries == 0:
            return 0.0
        return self.top_5_hits / self.total_queries


@dataclass(frozen=True)
class CatalogSyncStatusSnapshot:
    """Operational summary of sync row state."""

    counts: dict[str, int]
    oldest_pending_age_sec: float | None
    oldest_processing_age_sec: float | None
    repeated_failures: list[dict[str, Any]]


class CatalogSyncWorker:
    """Claim/process loop for durable catalog sync rows."""

    def __init__(
        self,
        *,
        repository: InvoiceImportRepository,
        embedding_client: EmbeddingClient,
        worker_id: str,
        lease_timeout: timedelta = timedelta(minutes=10),
    ) -> None:
        self.repository = repository
        self.embedding_client = embedding_client
        self.worker_id = worker_id
        self.lease_timeout = lease_timeout

    def process_one(self, *, now: Optional[datetime] = None) -> CatalogSyncJobResult:
        current_time = now or _utcnow()
        record = self.repository.claim_next_product_sync(
            worker_id=self.worker_id,
            now=current_time,
            lease_timeout=self.lease_timeout,
        )
        if record is None:
            return CatalogSyncJobResult(status="idle")

        embedding_text = build_catalog_embedding_text(record)
        try:
            embedding = self.embedding_client.embed(
                model=record.embedding_model,
                text=embedding_text,
            )
            self.repository.upsert_product_catalog_embedding(
                ProductCatalogEmbeddingRecordInput(
                    product_id=record.product_id,
                    product_snapshot_hash=record.product_snapshot_hash,
                    embedding_model=record.embedding_model,
                    embedding_text=embedding_text,
                    embedding=embedding,
                    metadata={
                        "name": record.name,
                        "barcode": record.barcode,
                        "category": record.category,
                        "uom": record.uom,
                        "supplier": record.supplier,
                        "price_eur": record.price_eur,
                        "price_50": record.price_50,
                        "price_70": record.price_70,
                        "price_100": record.price_100,
                        "markup": record.markup,
                        "invoice_number": record.invoice_number,
                    },
                )
            )
            self.repository.mark_product_sync_synced(sync_id=record.id, synced_at=current_time)
            return CatalogSyncJobResult(
                status="synced",
                sync_id=record.id,
                product_id=record.product_id,
            )
        except Exception as exc:
            next_attempt = record.attempt_count + 1
            self.repository.mark_product_sync_failed(
                sync_id=record.id,
                failed_at=current_time,
                last_error=str(exc),
                next_retry_at=current_time + compute_retry_delay(next_attempt),
            )
            return CatalogSyncJobResult(
                status="failed",
                sync_id=record.id,
                product_id=record.product_id,
                error=str(exc),
            )

    def sync_pending(self, *, limit: int = 100) -> list[CatalogSyncJobResult]:
        results: list[CatalogSyncJobResult] = []
        for _ in range(limit):
            result = self.process_one()
            if result.status == "idle":
                break
            results.append(result)
        return results


class CatalogRetrievalService:
    """Semantic retrieval over backend-owned product catalog embeddings."""

    def __init__(
        self,
        *,
        repository: InvoiceImportRepository,
        embedding_client: EmbeddingClient,
        default_embedding_model: str,
    ) -> None:
        self.repository = repository
        self.embedding_client = embedding_client
        self.default_embedding_model = default_embedding_model

    def query(
        self,
        text: str,
        *,
        top_k: int = 5,
        embedding_model: Optional[str] = None,
        mode: Literal["semantic", "lexical", "hybrid"] = "hybrid",
    ) -> CatalogQueryResult:
        model = embedding_model or self.default_embedding_model

        if mode == "lexical":
            raw_matches = self.repository.search_product_catalog_embeddings_lexical(
                query_text=text,
                embedding_model=model,
                top_k=top_k,
            )
        elif mode == "semantic":
            query_embedding = self.embedding_client.embed(model=model, text=text)
            raw_matches = self.repository.search_product_catalog_embeddings(
                query_embedding=query_embedding,
                embedding_model=model,
                top_k=top_k,
            )
        else:
            query_embedding = self.embedding_client.embed(model=model, text=text)

            def _semantic() -> list[ProductCatalogEmbeddingMatch]:
                return self.repository.search_product_catalog_embeddings(
                    query_embedding=query_embedding,
                    embedding_model=model,
                    top_k=top_k,
                )

            def _lexical() -> list[ProductCatalogEmbeddingMatch]:
                return self.repository.search_product_catalog_embeddings_lexical(
                    query_text=text,
                    embedding_model=model,
                    top_k=top_k,
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                fut_semantic = pool.submit(_semantic)
                fut_lexical = pool.submit(_lexical)
                semantic = fut_semantic.result()
                lexical = fut_lexical.result()

            raw_matches = rrf_merge(semantic, lexical, top_k=top_k)

        return CatalogQueryResult(
            query=text,
            embedding_model=model,
            top_k=top_k,
            search_mode=mode,
            matches=[
                CatalogRagMatch(
                    product_id=match.product_id,
                    product_snapshot_hash=match.product_snapshot_hash,
                    embedding_model=match.embedding_model,
                    score=match.score,
                    metadata=dict(match.metadata),
                    embedding_text=match.embedding_text,
                )
                for match in raw_matches
            ],
        )


class CatalogRagEvaluator:
    """Evaluation harness for representative catalog queries."""

    def __init__(self, retrieval_service: CatalogRetrievalService) -> None:
        self.retrieval_service = retrieval_service

    def evaluate(self, cases: list[CatalogEvalCase]) -> CatalogEvalResult:
        results: list[dict[str, Any]] = []
        top_1_hits = 0
        top_5_hits = 0
        for case in cases:
            query_result = self.retrieval_service.query(case.query, top_k=5)
            ranked_product_ids = [match.product_id for match in query_result.matches]
            top_1 = bool(ranked_product_ids[:1] and ranked_product_ids[0] == case.expected_product_id)
            top_5 = case.expected_product_id in ranked_product_ids[:5]
            if top_1:
                top_1_hits += 1
            if top_5:
                top_5_hits += 1
            results.append(
                {
                    "query": case.query,
                    "expected_product_id": case.expected_product_id,
                    "ranked_product_ids": ranked_product_ids,
                    "top_1_hit": top_1,
                    "top_5_hit": top_5,
                }
            )

        return CatalogEvalResult(
            total_queries=len(cases),
            top_1_hits=top_1_hits,
            top_5_hits=top_5_hits,
            cases=results,
        )


def load_eval_cases(path: Path) -> list[CatalogEvalCase]:
    """Load evaluation queries from a JSON fixture."""
    payload = json.loads(path.read_text())
    raw_cases = payload["queries"] if isinstance(payload, dict) else payload
    return [CatalogEvalCase(**raw_case) for raw_case in raw_cases]


def serialize_query_result(result: CatalogQueryResult) -> dict[str, Any]:
    """Convert retrieval results into JSON-friendly output."""
    return {
        "query": result.query,
        "embedding_model": result.embedding_model,
        "top_k": result.top_k,
        "search_mode": result.search_mode,
        "matches": [
            {
                "product_id": match.product_id,
                "product_snapshot_hash": match.product_snapshot_hash,
                "embedding_model": match.embedding_model,
                "score": match.score,
                "metadata": match.metadata,
                "embedding_text": match.embedding_text,
            }
            for match in result.matches
        ],
    }


def serialize_eval_result(result: CatalogEvalResult) -> dict[str, Any]:
    """Convert evaluation metrics into JSON-friendly output."""
    return {
        "total_queries": result.total_queries,
        "top_1_hits": result.top_1_hits,
        "top_5_hits": result.top_5_hits,
        "top_1_hit_rate": result.top_1_hit_rate,
        "top_5_hit_rate": result.top_5_hit_rate,
        "cases": result.cases,
    }


def build_sync_status_snapshot(
    repository: InvoiceImportRepository,
    *,
    now: Optional[datetime] = None,
) -> CatalogSyncStatusSnapshot:
    """Summarize sync row state for operational inspection."""
    current_time = now or _utcnow()
    records = repository.list_product_sync_records()
    counts = {
        "pending": 0,
        "processing": 0,
        "failed": 0,
        "synced": 0,
    }
    pending_ages: list[float] = []
    processing_ages: list[float] = []
    repeated_failures: list[dict[str, Any]] = []

    for record in records:
        counts[record.sync_status] = counts.get(record.sync_status, 0) + 1
        if record.sync_status == "pending":
            pending_ages.append((current_time - record.created_at).total_seconds())
        if record.sync_status == "processing" and record.claimed_at is not None:
            processing_ages.append((current_time - record.claimed_at).total_seconds())
        if record.sync_status == "failed" and record.attempt_count > 1:
            repeated_failures.append(
                {
                    "sync_id": record.id,
                    "product_id": record.product_id,
                    "attempt_count": record.attempt_count,
                    "last_error": record.last_error,
                    "next_retry_at": (
                        record.next_retry_at.isoformat()
                        if record.next_retry_at is not None
                        else None
                    ),
                }
            )

    return CatalogSyncStatusSnapshot(
        counts=counts,
        oldest_pending_age_sec=max(pending_ages) if pending_ages else None,
        oldest_processing_age_sec=max(processing_ages) if processing_ages else None,
        repeated_failures=repeated_failures,
    )


def serialize_sync_status_snapshot(snapshot: CatalogSyncStatusSnapshot) -> dict[str, Any]:
    """Convert sync status summary into JSON-friendly output."""
    return {
        "counts": snapshot.counts,
        "oldest_pending_age_sec": snapshot.oldest_pending_age_sec,
        "oldest_processing_age_sec": snapshot.oldest_processing_age_sec,
        "repeated_failures": snapshot.repeated_failures,
    }


def build_rag_worker(
    *,
    repository: InvoiceImportRepository,
    config: InvoiceConfig,
    worker_id: str,
    embedding_client: Optional[EmbeddingClient] = None,
) -> CatalogSyncWorker:
    """Build a sync worker from app-owned resources."""
    return CatalogSyncWorker(
        repository=repository,
        embedding_client=embedding_client or OpenAIEmbeddingClient(config),
        worker_id=worker_id,
    )


def build_retrieval_service(
    *,
    repository: InvoiceImportRepository,
    config: InvoiceConfig,
    embedding_client: Optional[EmbeddingClient] = None,
) -> CatalogRetrievalService:
    """Build a retrieval service from app-owned resources."""
    return CatalogRetrievalService(
        repository=repository,
        embedding_client=embedding_client or OpenAIEmbeddingClient(config),
        default_embedding_model=config.catalog_sync_embedding_model,
    )
