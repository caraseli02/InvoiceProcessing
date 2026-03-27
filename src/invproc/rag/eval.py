"""Evaluation workflow for backend-owned RAG retrieval."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import re
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
    search_mode: str = "hybrid"
    top_k: int = 0
    match_threshold: float = 0.0
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
        match_threshold: float | None = None,
    ) -> CatalogEvalResult:
        results: list[dict[str, Any]] = []
        top_1_hits = 0
        top_5_hits = 0
        top_k_hits = 0
        for case in cases:
            query_result = self.retrieval_service.query(
                case.query,
                top_k=top_k,
                mode=mode,
                match_threshold=match_threshold,
            )
            ranked_product_ids = [match.product_id for match in query_result.matches]
            embedding_texts = [match.embedding_text.lower() for match in query_result.matches]
            top_result = query_result.matches[0] if query_result.matches else None

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
                    "top_k": top_k,
                    "match_threshold": query_result.match_threshold,
                    "top_result_product_id": top_result.product_id if top_result is not None else "",
                    "top_result_embedding_text": (
                        top_result.embedding_text if top_result is not None else ""
                    ),
                    "top_result_score": top_result.score if top_result is not None else None,
                    "top_results": [
                        {
                            "product_id": match.product_id,
                            "score": match.score,
                            "embedding_text": match.embedding_text,
                        }
                        for match in query_result.matches[:5]
                    ],
                }
            )

        return CatalogEvalResult(
            total_queries=len(cases),
            top_1_hits=top_1_hits,
            top_5_hits=top_5_hits,
            top_k_hits=top_k_hits,
            search_mode=mode,
            top_k=top_k,
            match_threshold=query_result.match_threshold if cases else (match_threshold or 0.0),
            cases=results,
        )

    def evaluate_all_modes(
        self,
        cases: list[CatalogEvalCase],
        *,
        top_k: int = 10,
        match_threshold: float | None = None,
    ) -> CatalogModeComparisonResult:
        """Run evaluate() for all three search modes and return side-by-side results."""
        return CatalogModeComparisonResult(
            semantic=self.evaluate(
                cases,
                mode="semantic",
                top_k=top_k,
                match_threshold=match_threshold,
            ),
            lexical=self.evaluate(
                cases,
                mode="lexical",
                top_k=top_k,
                match_threshold=match_threshold,
            ),
            hybrid=self.evaluate(
                cases,
                mode="hybrid",
                top_k=top_k,
                match_threshold=match_threshold,
            ),
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


_SNAPSHOT_SCHEMA_VERSION = "rag-eval-snapshot-v1"
_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T\d{6}(?:\d+)?)?-")


def compute_eval_fixture_hash(path: Path) -> str:
    """Compute a stable fixture hash for compatibility checks."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_eval_snapshot(
    *,
    fixture_name: str,
    fixture_hash: str | None,
    result: CatalogEvalResult,
    mock: bool,
    generated_at: datetime | None = None,
    comparison_target: str | None = None,
) -> dict[str, Any]:
    """Build a self-describing snapshot artifact for one eval run."""
    timestamp = generated_at or datetime.now(timezone.utc)
    return {
        "schema_version": _SNAPSHOT_SCHEMA_VERSION,
        "generated_at": timestamp.isoformat(),
        "fixture_name": fixture_name,
        "fixture_hash": fixture_hash,
        "search_mode": result.search_mode,
        "top_k": result.top_k,
        "match_threshold": result.match_threshold,
        "mock": mock,
        "comparison_target": comparison_target,
        "result": serialize_eval_result(result),
    }


def build_eval_snapshot_filename(
    *,
    fixture_name: str,
    search_mode: str,
    generated_at: datetime | None = None,
) -> str:
    """Create a timestamped snapshot filename."""
    timestamp = (generated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H%M%S%f")
    fixture_stem = Path(fixture_name).stem
    return f"{timestamp}-{fixture_stem}-{search_mode}.json"


def load_eval_snapshot(path: Path) -> dict[str, Any]:
    """Load a saved eval snapshot artifact."""
    return dict(json.loads(path.read_text()))


def normalize_eval_snapshot(
    snapshot: dict[str, Any],
    *,
    path: Path | None = None,
    search_mode: str | None = None,
) -> dict[str, Any]:
    """Normalize legacy and current snapshot formats into one comparison shape."""
    if "result" in snapshot:
        normalized = dict(snapshot)
        normalized.setdefault("fixture_name", _infer_fixture_name(path))
        normalized.setdefault("fixture_hash", None)
        normalized.setdefault("search_mode", search_mode or normalized.get("search_mode", "hybrid"))
        return normalized

    if "by_mode" in snapshot:
        normalized_mode = search_mode or "hybrid"
        if normalized_mode not in snapshot["by_mode"]:
            raise ValueError(f"Snapshot does not include mode '{normalized_mode}'")
        return {
            "schema_version": "legacy-rag-eval-baseline",
            "generated_at": snapshot.get("generated_at"),
            "fixture_name": snapshot.get("fixture_name") or _infer_fixture_name(path),
            "fixture_hash": snapshot.get("fixture_hash"),
            "search_mode": normalized_mode,
            "top_k": snapshot.get("top_k"),
            "match_threshold": snapshot.get("match_threshold"),
            "mock": snapshot.get("mock"),
            "result": snapshot["by_mode"][normalized_mode],
        }

    if "top_1_hits" in snapshot and "cases" in snapshot:
        return {
            "schema_version": "bare-rag-eval-result",
            "generated_at": snapshot.get("generated_at"),
            "fixture_name": _infer_fixture_name(path),
            "fixture_hash": snapshot.get("fixture_hash"),
            "search_mode": search_mode or snapshot.get("search_mode", "hybrid"),
            "top_k": snapshot.get("top_k"),
            "match_threshold": snapshot.get("match_threshold"),
            "mock": snapshot.get("mock"),
            "result": snapshot,
        }

    raise ValueError("Unsupported eval snapshot format")


def find_latest_compatible_snapshot(
    directory: Path,
    *,
    fixture_name: str,
    fixture_hash: str,
    search_mode: str,
    top_k: int,
    match_threshold: float | None,
    mock: bool,
    exclude: Path | None = None,
) -> Path | None:
    """Find the newest compatible snapshot for the requested eval settings."""
    candidates = sorted(directory.glob("*.json"), reverse=True)
    for candidate in candidates:
        if exclude is not None and candidate.resolve() == exclude.resolve():
            continue
        try:
            normalized = normalize_eval_snapshot(
                load_eval_snapshot(candidate),
                path=candidate,
                search_mode=search_mode,
            )
        except (ValueError, json.JSONDecodeError):
            continue
        if not _snapshot_is_compatible(
            normalized,
            fixture_name=fixture_name,
            fixture_hash=fixture_hash,
            search_mode=search_mode,
            top_k=top_k,
            match_threshold=match_threshold,
            mock=mock,
        ):
            continue
        return candidate
    return None


def compare_eval_snapshots(
    *,
    current: dict[str, Any],
    baseline: dict[str, Any],
) -> dict[str, Any]:
    """Compare two normalized snapshots and classify case-level changes."""
    current_result = current["result"]
    baseline_result = baseline["result"]
    baseline_cases = {
        _case_identity(case): case
        for case in baseline_result.get("cases", [])
    }

    changes: list[dict[str, Any]] = []
    counts = {"regressed": 0, "improved": 0, "changed": 0, "unchanged": 0, "new": 0}
    for case in current_result.get("cases", []):
        identity = _case_identity(case)
        baseline_case = baseline_cases.get(identity)
        if baseline_case is None:
            classification = "new"
            counts[classification] += 1
            changes.append(
                {
                    "classification": classification,
                    "query": case["query"],
                    "expected": _case_expected_label(case),
                    "current": case,
                    "baseline": None,
                }
            )
            continue

        classification = _classify_case_change(current_case=case, baseline_case=baseline_case)
        counts[classification] += 1
        if classification == "unchanged":
            continue
        changes.append(
            {
                "classification": classification,
                "query": case["query"],
                "expected": _case_expected_label(case),
                "current": case,
                "baseline": baseline_case,
            }
        )

    counts["unchanged"] = max(
        0,
        current_result.get("total_queries", 0)
        - counts["regressed"]
        - counts["improved"]
        - counts["changed"]
        - counts["new"],
    )

    return {
        "baseline_fixture_name": baseline.get("fixture_name"),
        "baseline_generated_at": baseline.get("generated_at"),
        "current_fixture_name": current.get("fixture_name"),
        "current_generated_at": current.get("generated_at"),
        "summary": {
            "total_queries": current_result["total_queries"],
            "top_1_hits_delta": current_result["top_1_hits"] - baseline_result["top_1_hits"],
            "top_5_hits_delta": current_result["top_5_hits"] - baseline_result["top_5_hits"],
            "top_k_hits_delta": current_result["top_k_hits"] - baseline_result["top_k_hits"],
            "top_1_hit_rate_delta": current_result["top_1_hit_rate"] - baseline_result["top_1_hit_rate"],
            "top_5_hit_rate_delta": current_result["top_5_hit_rate"] - baseline_result["top_5_hit_rate"],
            "top_k_hit_rate_delta": current_result["top_k_hit_rate"] - baseline_result["top_k_hit_rate"],
            "counts": counts,
        },
        "cases": sorted(changes, key=_comparison_sort_key),
    }


def serialize_eval_result(result: CatalogEvalResult) -> dict[str, Any]:
    return {
        "total_queries": result.total_queries,
        "top_1_hits": result.top_1_hits,
        "top_5_hits": result.top_5_hits,
        "top_k_hits": result.top_k_hits,
        "top_1_hit_rate": result.top_1_hit_rate,
        "top_5_hit_rate": result.top_5_hit_rate,
        "top_k_hit_rate": result.top_k_hit_rate,
        "search_mode": result.search_mode,
        "top_k": result.top_k,
        "match_threshold": result.match_threshold,
        "cases": result.cases,
    }


def _snapshot_is_compatible(
    snapshot: dict[str, Any],
    *,
    fixture_name: str,
    fixture_hash: str,
    search_mode: str,
    top_k: int,
    match_threshold: float | None,
    mock: bool,
) -> bool:
    if snapshot.get("fixture_name") != fixture_name:
        return False
    if snapshot.get("fixture_hash") not in (None, fixture_hash):
        return False
    if snapshot.get("search_mode") != search_mode:
        return False
    if snapshot.get("top_k") not in (None, top_k):
        return False
    if snapshot.get("match_threshold") not in (None, match_threshold):
        return False
    if snapshot.get("mock") not in (None, mock):
        return False
    return True


def _infer_fixture_name(path: Path | None) -> str:
    if path is None:
        return ""
    name = _DATE_PREFIX_RE.sub("", path.name)
    if "-" in name and name.endswith(".json"):
        maybe_fixture, maybe_mode = name.rsplit("-", 1)
        if maybe_mode == "hybrid.json" or maybe_mode == "semantic.json" or maybe_mode == "lexical.json":
            return f"{maybe_fixture}.json"
    return name


def _case_identity(case: dict[str, Any]) -> str:
    return "|".join(
        [
            case.get("query", ""),
            case.get("expected_product_id", ""),
            case.get("expected_name", "").lower(),
        ]
    )


def _case_expected_label(case: dict[str, Any]) -> str:
    return case.get("expected_product_id") or case.get("expected_name") or "<unspecified>"


def _classify_case_change(
    *,
    current_case: dict[str, Any],
    baseline_case: dict[str, Any],
) -> str:
    current_score = (2 if current_case.get("top_1_hit") else 0) + (1 if current_case.get("top_5_hit") else 0)
    baseline_score = (2 if baseline_case.get("top_1_hit") else 0) + (1 if baseline_case.get("top_5_hit") else 0)
    if current_score > baseline_score:
        return "improved"
    if current_score < baseline_score:
        return "regressed"
    if current_case.get("top_result_product_id") != baseline_case.get("top_result_product_id"):
        return "changed"
    if current_case.get("ranked_product_ids", [])[:5] != baseline_case.get("ranked_product_ids", [])[:5]:
        return "changed"
    return "unchanged"


def _comparison_sort_key(change: dict[str, Any]) -> tuple[int, str]:
    priorities = {"regressed": 0, "improved": 1, "changed": 2, "new": 3, "unchanged": 4}
    return (priorities.get(change["classification"], 99), change["query"])
