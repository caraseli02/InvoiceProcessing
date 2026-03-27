"""Evaluation workflow for backend-owned RAG retrieval."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from invproc.rag.retrieval import CatalogRetrievalService


@dataclass(frozen=True)
class CatalogEvalCase:
    """One evaluation query and expected result."""

    query: str
    expected_product_id: str = ""
    expected_name: str = ""

    def __post_init__(self) -> None:
        if not self.expected_product_id and not self.expected_name:
            raise ValueError("CatalogEvalCase requires expected_product_id or expected_name")


@dataclass(frozen=True)
class CatalogEvalResult:
    """Aggregate evaluation metrics."""

    total_queries: int
    top_1_hits: int
    top_5_hits: int
    top_k_hits: int = 0
    cases: list[dict[str, Any]] = field(default_factory=list)

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

    @property
    def top_k_hit_rate(self) -> float:
        if self.total_queries == 0:
            return 0.0
        return self.top_k_hits / self.total_queries


@dataclass(frozen=True)
class CatalogModeComparisonResult:
    """Side-by-side eval metrics for all three search modes."""

    semantic: CatalogEvalResult
    lexical: CatalogEvalResult
    hybrid: CatalogEvalResult


class CatalogRagEvaluator:
    """Evaluation harness for representative catalog queries."""

    def __init__(self, retrieval_service: CatalogRetrievalService) -> None:
        self.retrieval_service = retrieval_service

    def evaluate(
        self,
        cases: list[CatalogEvalCase],
        *,
        mode: Literal["semantic", "lexical", "hybrid"] = "hybrid",
        top_k: int = 10,
    ) -> CatalogEvalResult:
        results: list[dict[str, Any]] = []
        top_1_hits = 0
        top_5_hits = 0
        top_k_hits = 0
        for case in cases:
            query_result = self.retrieval_service.query(case.query, top_k=top_k, mode=mode)
            ranked_product_ids = [match.product_id for match in query_result.matches]
            embedding_texts = [match.embedding_text.lower() for match in query_result.matches]

            if case.expected_product_id:
                top_1 = bool(ranked_product_ids[:1] and ranked_product_ids[0] == case.expected_product_id)
                top_5 = case.expected_product_id in ranked_product_ids[:5]
                top_k_hit = case.expected_product_id in ranked_product_ids
            else:
                needle = case.expected_name.lower()
                top_1 = bool(embedding_texts[:1] and needle in embedding_texts[0])
                top_5 = any(needle in t for t in embedding_texts[:5])
                top_k_hit = any(needle in t for t in embedding_texts)

            if top_1:
                top_1_hits += 1
            if top_5:
                top_5_hits += 1
            if top_k_hit:
                top_k_hits += 1
            results.append(
                {
                    "query": case.query,
                    "expected_product_id": case.expected_product_id,
                    "expected_name": case.expected_name,
                    "ranked_product_ids": ranked_product_ids,
                    "top_1_hit": top_1,
                    "top_5_hit": top_5,
                    "top_k_hit": top_k_hit,
                    "search_mode": mode,
                }
            )

        return CatalogEvalResult(
            total_queries=len(cases),
            top_1_hits=top_1_hits,
            top_5_hits=top_5_hits,
            top_k_hits=top_k_hits,
            cases=results,
        )

    def evaluate_all_modes(
        self, cases: list[CatalogEvalCase], *, top_k: int = 10
    ) -> CatalogModeComparisonResult:
        """Run evaluate() for all three search modes and return side-by-side results."""
        return CatalogModeComparisonResult(
            semantic=self.evaluate(cases, mode="semantic", top_k=top_k),
            lexical=self.evaluate(cases, mode="lexical", top_k=top_k),
            hybrid=self.evaluate(cases, mode="hybrid", top_k=top_k),
        )


_EVAL_CASE_KEYS: frozenset[str] = frozenset({"query", "expected_product_id", "expected_name"})


def _case_from_dict(d: dict[str, Any]) -> CatalogEvalCase:
    """Construct a CatalogEvalCase from a raw dict, silently ignoring unknown keys."""
    return CatalogEvalCase(**{k: v for k, v in d.items() if k in _EVAL_CASE_KEYS})


def load_eval_cases(path: Path) -> list[CatalogEvalCase]:
    """Load evaluation queries from a JSON fixture."""
    payload = json.loads(path.read_text())
    raw_cases = payload["queries"] if isinstance(payload, dict) else payload
    return [_case_from_dict(raw_case) for raw_case in raw_cases]
