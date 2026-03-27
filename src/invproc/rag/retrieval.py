"""Retrieval workflow for backend-owned RAG catalog search."""

from __future__ import annotations

import concurrent.futures
import hashlib
import math
from dataclasses import dataclass
from typing import Any, Literal, Optional, Protocol

from openai import OpenAI

from invproc.config import InvoiceConfig
from invproc.repositories.base import InvoiceImportRepository, ProductCatalogEmbeddingMatch


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
    """Merge semantic and lexical result lists via Reciprocal Rank Fusion."""
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
    match_threshold: float = 0.0

    @property
    def has_match(self) -> bool:
        return bool(self.matches)


class CatalogRetrievalService:
    """Semantic retrieval over backend-owned product catalog embeddings."""

    def __init__(
        self,
        *,
        repository: InvoiceImportRepository,
        embedding_client: EmbeddingClient,
        default_embedding_model: str,
        match_threshold: float = 0.0,
    ) -> None:
        self.repository = repository
        self.embedding_client = embedding_client
        self.default_embedding_model = default_embedding_model
        self.match_threshold = match_threshold

    def query(
        self,
        text: str,
        *,
        top_k: int = 5,
        embedding_model: Optional[str] = None,
        mode: Literal["semantic", "lexical", "hybrid"] = "hybrid",
        match_threshold: Optional[float] = None,
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

        effective_threshold = match_threshold if match_threshold is not None else self.match_threshold
        return CatalogQueryResult(
            query=text,
            embedding_model=model,
            top_k=top_k,
            search_mode=mode,
            match_threshold=effective_threshold,
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
                if match.score >= effective_threshold
            ],
        )
