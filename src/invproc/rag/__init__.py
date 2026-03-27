"""Backend-owned RAG services for catalog sync, retrieval, and evaluation."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from invproc.rag.eval import (
        build_eval_snapshot,
        build_eval_snapshot_filename,
        CatalogEvalCase,
        CatalogEvalResult,
        CatalogModeComparisonResult,
        CatalogRagEvaluator,
        compare_eval_snapshots,
        compute_eval_fixture_hash,
        find_latest_compatible_snapshot,
        load_eval_snapshot,
        normalize_eval_snapshot,
        _case_from_dict,
        load_eval_cases,
    )
    from invproc.rag.retrieval import (
        CatalogQueryResult,
        CatalogRagMatch,
        CatalogRetrievalService,
        EmbeddingClient,
        OpenAIEmbeddingClient,
        cosine_similarity,
        rrf_merge,
    )
    from invproc.rag.sync import (
        CatalogSyncJobResult,
        CatalogSyncStatusSnapshot,
        CatalogSyncWorker,
        build_catalog_embedding_text,
        build_sync_status_snapshot,
        compute_retry_delay,
    )
    from invproc.rag.transport import (
        build_rag_worker,
        build_retrieval_service,
        serialize_eval_result,
        serialize_mode_comparison,
        serialize_query_result,
        serialize_sync_status_snapshot,
    )

_EXPORT_TO_MODULE = {
    "CatalogEvalCase": "invproc.rag.eval",
    "CatalogEvalResult": "invproc.rag.eval",
    "CatalogModeComparisonResult": "invproc.rag.eval",
    "CatalogQueryResult": "invproc.rag.retrieval",
    "CatalogRagEvaluator": "invproc.rag.eval",
    "CatalogRagMatch": "invproc.rag.retrieval",
    "CatalogRetrievalService": "invproc.rag.retrieval",
    "CatalogSyncJobResult": "invproc.rag.sync",
    "CatalogSyncStatusSnapshot": "invproc.rag.sync",
    "CatalogSyncWorker": "invproc.rag.sync",
    "EmbeddingClient": "invproc.rag.retrieval",
    "OpenAIEmbeddingClient": "invproc.rag.retrieval",
    "_case_from_dict": "invproc.rag.eval",
    "build_catalog_embedding_text": "invproc.rag.sync",
    "build_eval_snapshot": "invproc.rag.eval",
    "build_eval_snapshot_filename": "invproc.rag.eval",
    "build_rag_worker": "invproc.rag.transport",
    "build_retrieval_service": "invproc.rag.transport",
    "build_sync_status_snapshot": "invproc.rag.sync",
    "compare_eval_snapshots": "invproc.rag.eval",
    "compute_eval_fixture_hash": "invproc.rag.eval",
    "compute_retry_delay": "invproc.rag.sync",
    "cosine_similarity": "invproc.rag.retrieval",
    "find_latest_compatible_snapshot": "invproc.rag.eval",
    "load_eval_snapshot": "invproc.rag.eval",
    "load_eval_cases": "invproc.rag.eval",
    "normalize_eval_snapshot": "invproc.rag.eval",
    "rrf_merge": "invproc.rag.retrieval",
    "serialize_eval_result": "invproc.rag.transport",
    "serialize_mode_comparison": "invproc.rag.transport",
    "serialize_query_result": "invproc.rag.transport",
    "serialize_sync_status_snapshot": "invproc.rag.transport",
}

__all__ = [
    "CatalogEvalCase",
    "CatalogEvalResult",
    "CatalogModeComparisonResult",
    "CatalogQueryResult",
    "CatalogRagEvaluator",
    "CatalogRagMatch",
    "CatalogRetrievalService",
    "CatalogSyncJobResult",
    "CatalogSyncStatusSnapshot",
    "CatalogSyncWorker",
    "EmbeddingClient",
    "OpenAIEmbeddingClient",
    "_case_from_dict",
    "build_catalog_embedding_text",
    "build_eval_snapshot",
    "build_eval_snapshot_filename",
    "build_rag_worker",
    "build_retrieval_service",
    "build_sync_status_snapshot",
    "compare_eval_snapshots",
    "compute_eval_fixture_hash",
    "compute_retry_delay",
    "cosine_similarity",
    "find_latest_compatible_snapshot",
    "load_eval_snapshot",
    "load_eval_cases",
    "normalize_eval_snapshot",
    "rrf_merge",
    "serialize_eval_result",
    "serialize_mode_comparison",
    "serialize_query_result",
    "serialize_sync_status_snapshot",
]


def __getattr__(name: str) -> Any:
    """Lazily resolve compatibility exports without importing the full RAG stack."""
    module_name = _EXPORT_TO_MODULE.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
