"""Transport helpers and builders for backend-owned RAG workflows."""

from __future__ import annotations

from typing import Any, Optional

from invproc.config import InvoiceConfig
from invproc.rag.eval import CatalogModeComparisonResult, serialize_eval_result
from invproc.rag.retrieval import (
    CatalogQueryResult,
    CatalogRetrievalService,
    EmbeddingClient,
    OpenAIEmbeddingClient,
)
from invproc.rag.sync import CatalogSyncStatusSnapshot, CatalogSyncWorker
from invproc.repositories.base import InvoiceImportRepository


def serialize_query_result(result: CatalogQueryResult) -> dict[str, Any]:
    """Convert retrieval results into JSON-friendly output."""
    return {
        "query": result.query,
        "embedding_model": result.embedding_model,
        "top_k": result.top_k,
        "search_mode": result.search_mode,
        "match_threshold": result.match_threshold,
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


def serialize_mode_comparison(comparison: CatalogModeComparisonResult) -> dict[str, Any]:
    """Convert a multi-mode comparison into a JSON-friendly structure."""
    return {
        "summary": {
            mode: {
                "top_1_hit_rate": getattr(comparison, mode).top_1_hit_rate,
                "top_5_hit_rate": getattr(comparison, mode).top_5_hit_rate,
                "top_1_hits": getattr(comparison, mode).top_1_hits,
                "top_5_hits": getattr(comparison, mode).top_5_hits,
                "total_queries": getattr(comparison, mode).total_queries,
            }
            for mode in ("semantic", "lexical", "hybrid")
        },
        "by_mode": {
            mode: serialize_eval_result(getattr(comparison, mode))
            for mode in ("semantic", "lexical", "hybrid")
        },
    }


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
        match_threshold=config.rag_match_threshold,
    )
