-- Re-create the vector similarity search RPC.
-- This function may have been lost; applying this is idempotent (CREATE OR REPLACE).

create or replace function match_product_catalog_embeddings(
    p_query_embedding vector(1536),
    p_embedding_model text,
    p_match_count     integer default 5
) returns table (
    product_id            text,
    product_snapshot_hash text,
    embedding_model       text,
    embedding_text        text,
    metadata              jsonb,
    score                 double precision
)
language sql stable security definer
set search_path = public
as $$
    select
        product_catalog_embeddings.product_id,
        product_catalog_embeddings.product_snapshot_hash,
        product_catalog_embeddings.embedding_model,
        product_catalog_embeddings.embedding_text,
        product_catalog_embeddings.metadata,
        1 - (product_catalog_embeddings.embedding <=> p_query_embedding) as score
    from product_catalog_embeddings
    where product_catalog_embeddings.embedding_model = p_embedding_model
    order by product_catalog_embeddings.embedding <=> p_query_embedding
    limit p_match_count;
$$;
