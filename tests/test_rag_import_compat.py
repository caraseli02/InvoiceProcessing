"""Compatibility checks for the invproc.rag package entrypoint."""

from __future__ import annotations

import importlib
import sys


def test_invproc_rag_entrypoint_re_exports_expected_symbols() -> None:
    module = importlib.import_module("invproc.rag")

    expected_symbols = [
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
        "build_rag_worker",
        "build_retrieval_service",
        "build_sync_status_snapshot",
        "compute_retry_delay",
        "cosine_similarity",
        "load_eval_cases",
        "rrf_merge",
        "serialize_eval_result",
        "serialize_mode_comparison",
        "serialize_query_result",
        "serialize_sync_status_snapshot",
    ]

    for symbol in expected_symbols:
        assert hasattr(module, symbol), f"invproc.rag should export {symbol}"


def test_invproc_rag_package_import_is_lazy() -> None:
    for module_name in [
        "invproc.rag",
        "invproc.rag.eval",
        "invproc.rag.retrieval",
        "invproc.rag.sync",
        "invproc.rag.transport",
    ]:
        sys.modules.pop(module_name, None)

    importlib.import_module("invproc.rag")

    assert "invproc.rag.eval" not in sys.modules
    assert "invproc.rag.retrieval" not in sys.modules
    assert "invproc.rag.sync" not in sys.modules
    assert "invproc.rag.transport" not in sys.modules
