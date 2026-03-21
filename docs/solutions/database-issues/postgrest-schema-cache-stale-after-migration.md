---
name: PostgREST schema cache goes stale after applying a new SQL migration
description: Applying a new SQL function migration can cause PostgREST to partially drop existing functions from its schema cache, making previously working RPC calls return 404
type: solution
category: database-issues
date: 2026-03-21
tags: [supabase, postgrest, schema-cache, migration, rpc, pgvector]
---

## Problem

After applying a new SQL migration that adds a function (e.g., `search_product_catalog_embeddings_lexical`), a previously working RPC (`match_product_catalog_embeddings`) starts returning 404 from PostgREST:

```
Could not find the function public.match_product_catalog_embeddings(...)
 in the schema cache
```

The function still exists in the database (`\df` confirms it), but PostgREST no longer sees it.

## Root Cause

PostgREST maintains an in-process schema cache. When a migration runs, PostgREST may partially reload the cache. If the reload is interrupted or the cache is partially invalidated, existing functions can be dropped from the cache even though they remain in the database.

`NOTIFY pgrst, 'reload schema'` triggers a reload but does not guarantee all previously-known functions survive the reload if the pg_catalog state was temporarily inconsistent during migration execution.

## Solution

Create a follow-up migration (or append to the same migration) that issues `CREATE OR REPLACE FUNCTION` for the affected function. This forces PostgREST to re-register the function in its schema cache on the next reload.

```sql
-- supabase/migrations/008_restore_vector_search_rpc.sql
-- Re-create the vector similarity search RPC.
-- This function may have been lost; applying this is idempotent (CREATE OR REPLACE).

CREATE OR REPLACE FUNCTION match_product_catalog_embeddings(
    p_query_embedding vector(1536),
    p_embedding_model text,
    p_match_count     integer DEFAULT 5
) RETURNS TABLE (
    product_id            text,
    product_snapshot_hash text,
    embedding_model       text,
    embedding_text        text,
    metadata              jsonb,
    score                 double precision
)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public
AS $$
    SELECT
        product_catalog_embeddings.product_id,
        product_catalog_embeddings.product_snapshot_hash,
        product_catalog_embeddings.embedding_model,
        product_catalog_embeddings.embedding_text,
        product_catalog_embeddings.metadata,
        1 - (product_catalog_embeddings.embedding <=> p_query_embedding) AS score
    FROM product_catalog_embeddings
    WHERE product_catalog_embeddings.embedding_model = p_embedding_model
    ORDER BY product_catalog_embeddings.embedding <=> p_query_embedding
    LIMIT p_match_count;
$$;
```

`CREATE OR REPLACE` is idempotent — safe to run multiple times.

## Prevention

- When adding a new function to a schema that already has related functions (same table, same module), always include `CREATE OR REPLACE` for those related functions in the same migration or a follow-up.
- After every Supabase migration that touches functions, verify all RPCs via a quick smoke test before closing the PR.
- Keep migration files small and focused — one concern per file — so cache invalidation scope is predictable.
- Add the function names of all critical RPCs to the integration test suite so a schema cache drop surfaces immediately.

```python
# example assertion in integration tests
def test_match_product_catalog_embeddings_rpc_is_callable(supabase_client):
    # just verifying the RPC is visible to PostgREST
    response = supabase_client.rpc(
        "match_product_catalog_embeddings",
        {"p_query_embedding": [0.0] * 1536, "p_embedding_model": "text-embedding-3-small", "p_match_count": 1},
    ).execute()
    assert response.data is not None
```
