---
name: Hybrid RAG search combining BM25 lexical and vector semantic search with RRF merging
description: How to implement hybrid search (BM25 + pgvector cosine) merged via Reciprocal Rank Fusion for multilingual product catalog retrieval
type: solution
category: architecture-issues
date: 2026-03-21
tags: [rag, bm25, vector-search, rrf, hybrid-search, pgvector, supabase, full-text-search]
---

## Problem

Semantic (vector) search alone fails for exact-term lookups: barcodes, SKUs, incident IDs, product codes. A query like `"barcode 4841359000688"` returns near-zero cosine similarity against any vector — the embedding captures meaning, not exact character sequences.

Conversely, lexical (BM25/FTS) search alone fails for paraphrase queries: `"re-order the yogurt from metro"` won't match `"Yogurt Natural 3.5%"` if the user phrased it differently.

Wholesale ordering queries mix both patterns in a single interaction.

## Root Cause

Pure semantic retrieval encodes meaning into a dense vector. Rare exact tokens (barcodes, SKUs) contribute near-zero weight to the embedding relative to high-frequency semantic tokens — they vanish into noise.

## Solution

Three-mode hybrid search:

| Mode | Description |
|---|---|
| `semantic` | Vector cosine similarity only (pgvector `<=>` operator) |
| `lexical` | BM25/full-text search only (PostgreSQL FTS or in-memory BM25) |
| `hybrid` | Both run in parallel; merged via Reciprocal Rank Fusion (RRF, k=60) — **default** |

### Reciprocal Rank Fusion

```python
def rrf_merge(
    semantic_matches: list[ProductCatalogEmbeddingMatch],
    lexical_matches: list[ProductCatalogEmbeddingMatch],
    *,
    k: int = 60,
    top_k: int = 5,
) -> list[ProductCatalogEmbeddingMatch]:
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
            product_id=pid,
            product_snapshot_hash=records[pid].product_snapshot_hash,
            embedding_model=records[pid].embedding_model,
            embedding_text=records[pid].embedding_text,
            metadata=records[pid].metadata,
            score=scores[pid],
        )
        for pid in sorted_ids
    ]
```

### Supabase SQL for lexical search

```sql
-- GIN index so tsvector filtering is fast at catalog scale
CREATE INDEX IF NOT EXISTS product_catalog_embeddings_fts_idx
    ON product_catalog_embeddings
    USING gin (to_tsvector('simple', embedding_text));

-- 'simple' dictionary: tokenises without stemming — correct for multilingual
-- product names, barcodes, and SKU codes mixed in a single text field
CREATE OR REPLACE FUNCTION search_product_catalog_embeddings_lexical(
    p_query_text      text,
    p_embedding_model text,
    p_match_count     int
)
RETURNS TABLE (
    product_id             text,
    product_snapshot_hash  text,
    embedding_model        text,
    embedding_text         text,
    metadata               jsonb,
    score                  float
)
LANGUAGE sql STABLE AS $$
    SELECT
        product_id, product_snapshot_hash, embedding_model, embedding_text, metadata,
        ts_rank(
            to_tsvector('simple', embedding_text),
            plainto_tsquery('simple', p_query_text)
        )::float AS score
    FROM product_catalog_embeddings
    WHERE
        embedding_model = p_embedding_model
        AND to_tsvector('simple', embedding_text) @@ plainto_tsquery('simple', p_query_text)
    ORDER BY score DESC
    LIMIT p_match_count;
$$;
```

**Why `'simple'` dictionary?** It tokenises without language-specific stemming — correct for multilingual product names, barcodes, and SKU codes in one column.

### In-memory BM25 (for non-Supabase backend)

```python
def _bm25_scores(
    query_text: str,
    documents: list[str],
    k1: float = 1.5,
    b: float = 0.75,
) -> list[float]:
    query_tokens = query_text.lower().split()
    tokenized = [doc.lower().split() for doc in documents]
    n = len(documents)
    if n == 0:
        return []
    avgdl = sum(len(t) for t in tokenized) / n
    df: dict[str, int] = {}
    for doc_tokens in tokenized:
        for token in set(doc_tokens):
            df[token] = df.get(token, 0) + 1
    scores = []
    for doc_tokens in tokenized:
        tf_map: dict[str, int] = {}
        for token in doc_tokens:
            tf_map[token] = tf_map.get(token, 0) + 1
        dl = len(doc_tokens)
        score = 0.0
        for token in query_tokens:
            if token not in df:
                continue
            tf = tf_map.get(token, 0)
            idf = math.log((n - df[token] + 0.5) / (df[token] + 0.5) + 1.0)
            score += idf * (tf * (k1 + 1.0)) / (tf + k1 * (1.0 - b + b * dl / avgdl))
        scores.append(score)
    return scores
```

### Concurrent dispatch in hybrid mode

The two repository calls are independent — dispatch them in parallel with `ThreadPoolExecutor` so hybrid latency equals the slower of the two calls, not their sum:

```python
from concurrent.futures import ThreadPoolExecutor

# inside CatalogRetrievalService.query(), mode == "hybrid" branch:
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
```

**Why `ThreadPoolExecutor` and not `asyncio.gather`?** The repository methods are synchronous blocking calls (supabase-py / psycopg2). `asyncio.gather` does not parallelize sync functions without `loop.run_in_executor`. `ThreadPoolExecutor` gives each call its own thread and joins before continuing. Note: in the in-memory backend both calls serialize on `_lock` regardless — the speedup only applies in the Supabase backend.

See also: `docs/solutions/architecture-issues/hybrid-search-concurrent-dispatch-rag-eval-endpoint.md`

### API and CLI exposure

- `CatalogQueryRequest.search_mode: Literal["semantic", "lexical", "hybrid"] = "hybrid"`
- CLI: `--mode hybrid` on `rag query`, `--query-mode hybrid` on `rag ingest-invoice`
- Response always includes `"search_mode"` so callers can inspect which strategy was used

## Prevention

- Add `search_mode` to the response contract early; callers should always be able to inspect it.
- Default to `hybrid` — it handles both fuzzy NL and exact-code queries without threshold tuning.
- Use `'simple'` FTS dictionary for multilingual product catalogs; language-specific stemming breaks barcode/SKU lookup.
- Add a repository Protocol method for lexical search alongside the vector search method — this enforces both backends stay in sync.
- Test each mode independently: semantic finds yogurt by description; lexical finds by exact barcode; hybrid surfaces both.
