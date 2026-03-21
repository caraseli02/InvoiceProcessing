---
name: hybrid-search-concurrent-dispatch-rag-eval-endpoint
description: Two architectural improvements to the RAG pipeline — parallelizing the two independent DB calls in hybrid search mode with ThreadPoolExecutor, and adding POST /internal/rag/eval to close the CLI-only evaluation gap.
type: solution
category: architecture-issues
date: 2026-03-21
tags: [rag, hybrid-search, concurrency, threadpoolexecutor, performance, agent-native, eval, fastapi, api-parity]
---

## Problems

### 1. Hybrid mode doubled DB latency

`CatalogRetrievalService.query()` in hybrid mode ran semantic search then lexical search sequentially — two independent network round trips to Supabase, one after the other. No data from the first call feeds the second. The default search mode was hybrid, so every query paid full round-trip latency twice (~40–200 ms instead of ~20–100 ms).

### 2. `rag eval` was CLI-only

`python -m invproc rag eval <fixtures.json>` accepted a local filesystem path. There was no API equivalent. CI agents and external orchestrators could not trigger retrieval quality evaluation without SSH access to the host. The RAG contract document explicitly requires an evaluation surface for pre-frontend validation.

## Root Causes

**Hybrid latency:** The two repository calls were written sequentially. Since `CatalogRetrievalService.query()` is a synchronous method dispatched via `run_in_threadpool` from the FastAPI endpoint, `asyncio.gather` would not parallelize them without `run_in_executor`. `ThreadPoolExecutor` is the correct tool for parallelizing two blocking DB calls from within a sync method.

**Eval gap:** The command was added to the CLI first, reading a local file for convenience. No equivalent route was added to `api.py` at the time, leaving the capability inaccessible to remote callers.

## Solution

### 1. Concurrent dispatch in hybrid mode

```python
# rag.py
from concurrent.futures import ThreadPoolExecutor

class CatalogRetrievalService:
    def query(self, text: str, *, top_k: int = 5, mode: Literal["semantic", "lexical", "hybrid"] = "hybrid", ...) -> CatalogQueryResult:
        model = embedding_model or self.default_embedding_model
        query_embedding = self.embedding_client.embed(model=model, text=text)

        if mode == "semantic":
            matches = self.repository.search_product_catalog_embeddings(
                query_embedding=query_embedding, embedding_model=model, top_k=top_k,
            )
        elif mode == "lexical":
            matches = self.repository.search_product_catalog_embeddings_lexical(
                query_text=text, embedding_model=model, top_k=top_k,
            )
        else:  # hybrid
            with ThreadPoolExecutor(max_workers=2) as pool:
                sem_fut = pool.submit(
                    self.repository.search_product_catalog_embeddings,
                    query_embedding=query_embedding, embedding_model=model, top_k=top_k,
                )
                lex_fut = pool.submit(
                    self.repository.search_product_catalog_embeddings_lexical,
                    query_text=text, embedding_model=model, top_k=top_k,
                )
                semantic_matches = sem_fut.result()
                lexical_matches = lex_fut.result()
            matches = list(rrf_merge(semantic_matches, lexical_matches, top_k=top_k))

        return CatalogQueryResult(query=text, embedding_model=model, top_k=top_k, matches=[...])
```

**Key detail:** `ThreadPoolExecutor` with `max_workers=2` is the right tool here — not `asyncio.gather`. The repository methods are synchronous blocking calls (supabase-py / psycopg2). `asyncio.gather` on sync functions would not parallelize them without `loop.run_in_executor`. `ThreadPoolExecutor` gives each call its own thread and joins on both before continuing.

**Memory backend note:** In the in-memory backend, both calls acquire `_lock` — they serialize on the lock regardless of concurrent dispatch. The concurrent dispatch only provides a speedup in the Supabase backend (two independent network connections).

---

### 2. POST /internal/rag/eval endpoint

Cases are passed inline in the request body — no filesystem path needed:

```python
# api.py
class EvalRequest(BaseModel):
    cases: list[dict[str, Any]]
    embedding_model: Optional[str] = None

@router.post("/internal/rag/eval")
async def run_rag_eval(
    payload: EvalRequest,
    user: dict[str, Any] = Depends(verify_internal_caller),
    retrieval_service: CatalogRetrievalService = Depends(get_rag_retrieval_service),
) -> dict[str, Any]:
    result = retrieval_service.run_eval(
        payload.cases,
        embedding_model=payload.embedding_model,
    )
    return serialize_eval_result(result)
```

**Key detail:** Uses `verify_internal_caller` (not `verify_supabase_jwt`) — eval results expose retrieval internals (scores, ranked product IDs) and must not be reachable by end users. The fixture cases are passed inline so the caller owns the eval dataset with no shared filesystem dependency.

**CLI usage unchanged** — `rag eval <fixtures.json>` reads the file locally and calls `retrieval_service.run_eval()` directly, now sharing the same service method as the API endpoint.

## Prevention

**Concurrent independent calls:** Any two I/O calls with no data dependency must not be sequential. In code review, when two repository/HTTP calls appear on consecutive lines in the same function, verify whether the second call depends on the first result. If not, flag for concurrent dispatch.

```python
# test to detect sequential execution (timing-based)
def test_hybrid_search_dispatches_concurrently(monkeypatch):
    import threading, time
    call_log = []

    real_semantic = repo.search_product_catalog_embeddings
    real_lexical = repo.search_product_catalog_embeddings_lexical

    def slow_semantic(**kw):
        call_log.append(("semantic_start", time.monotonic()))
        time.sleep(0.05)
        return real_semantic(**kw)

    def slow_lexical(**kw):
        call_log.append(("lexical_start", time.monotonic()))
        time.sleep(0.05)
        return real_lexical(**kw)

    monkeypatch.setattr(repo, "search_product_catalog_embeddings", slow_semantic)
    monkeypatch.setattr(repo, "search_product_catalog_embeddings_lexical", slow_lexical)

    start = time.monotonic()
    service.query("test", mode="hybrid")
    elapsed = time.monotonic() - start

    # Sequential would be >= 0.10; concurrent should be < 0.08
    assert elapsed < 0.08
```

**API parity:** When adding a CLI command that an agent or CI job might need to call remotely, always add a corresponding API route in the same PR. Rule: if the command reads from a local file path, the API counterpart must accept the content inline in the request body.

```python
# parity test pattern
def test_eval_reachable_via_api_not_only_cli(client, internal_headers, sample_eval_cases):
    response = client.post(
        "/internal/rag/eval",
        json={"cases": sample_eval_cases},
        headers=internal_headers,
    )
    assert response.status_code == 200
    assert "results" in response.json()
```

## Related docs

- `docs/solutions/architecture-issues/hybrid-rag-bm25-vector-rrf-search-mode.md` — documents what the two parallel calls produce (BM25 + pgvector) and how RRF merges the results; this doc covers *how* they are dispatched concurrently
- `docs/solutions/security-issues/pydantic-secretstr-internal-endpoint-auth-hardening.md` — the `/internal/rag/eval` endpoint uses `verify_internal_caller` documented there
- `docs/solutions/integration-issues/supabase-backed-rag-persistence-needed-rls-atomic-queue-and-api-parity-20260320.md` — introduced the other `/internal/rag/*` endpoints; the eval endpoint follows the same parity principle documented there
