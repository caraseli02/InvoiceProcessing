-- Lexical (full-text) search over product_catalog_embeddings.
-- Complements the existing match_product_catalog_embeddings vector RPC.
-- Used by the hybrid search path in CatalogRetrievalService.

-- GIN index so tsvector filtering is fast at catalog scale.
create index if not exists product_catalog_embeddings_fts_idx
    on product_catalog_embeddings
    using gin (to_tsvector('simple', embedding_text));

-- RPC called by SupabaseInvoiceImportRepository.search_product_catalog_embeddings_lexical().
-- 'simple' dictionary: tokenises without stemming — correct for multilingual product
-- names, barcodes, and SKU codes mixed in a single text field.
create or replace function search_product_catalog_embeddings_lexical(
    p_query_text      text,
    p_embedding_model text,
    p_match_count     int
)
returns table (
    product_id             text,
    product_snapshot_hash  text,
    embedding_model        text,
    embedding_text         text,
    metadata               jsonb,
    score                  float
)
language sql stable as $$
    select
        product_id,
        product_snapshot_hash,
        embedding_model,
        embedding_text,
        metadata,
        ts_rank(
            to_tsvector('simple', embedding_text),
            plainto_tsquery('simple', p_query_text)
        )::float as score
    from product_catalog_embeddings
    where
        embedding_model = p_embedding_model
        and to_tsvector('simple', embedding_text) @@ plainto_tsquery('simple', p_query_text)
    order by score desc
    limit p_match_count;
$$;
