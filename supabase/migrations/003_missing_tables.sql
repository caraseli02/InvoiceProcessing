-- Create the three tables missing from the existing Supabase project.
-- products and stock_movements already exist — skipped here.

create extension if not exists "pgcrypto";
create extension if not exists "vector";

-- ─────────────────────────────────────────────
-- invoice_import_runs  (idempotency store)
-- ─────────────────────────────────────────────
create table if not exists invoice_import_runs (
    id               uuid primary key default gen_random_uuid(),
    idempotency_key  text not null unique,
    request_hash     text not null,
    response_payload jsonb not null,
    status           text not null default 'completed',
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);

-- ─────────────────────────────────────────────
-- product_embedding_sync  (sync queue)
-- ─────────────────────────────────────────────
create table if not exists product_embedding_sync (
    id                    uuid primary key default gen_random_uuid(),
    product_id            text not null,
    product_snapshot_hash text not null,
    embedding_model       text not null,
    name                  text not null,
    barcode               text,
    category              text,
    uom                   text,
    supplier              text,
    price_eur             numeric,
    price_50              numeric,
    price_70              numeric,
    price_100             numeric,
    markup                integer,
    source_import_id      text not null,
    source_row_id         text not null,
    invoice_number        text,
    sync_status           text not null default 'pending'
                              check (sync_status in ('pending', 'processing', 'synced', 'failed')),
    attempt_count         integer not null default 0 check (attempt_count >= 0),
    last_error            text,
    claimed_at            timestamptz,
    claimed_by            text,
    next_retry_at         timestamptz,
    last_synced_at        timestamptz,
    created_at            timestamptz not null default now(),
    updated_at            timestamptz not null default now(),

    constraint product_embedding_sync_unique_snapshot
        unique (product_id, product_snapshot_hash)
);

create index if not exists product_sync_status_retry_idx
    on product_embedding_sync (sync_status, next_retry_at, created_at);

create index if not exists product_sync_product_id_idx
    on product_embedding_sync (product_id, created_at desc);

-- ─────────────────────────────────────────────
-- product_catalog_embeddings  (vector store)
-- text-embedding-3-small → 1536 dimensions
-- ─────────────────────────────────────────────
create table if not exists product_catalog_embeddings (
    id                    uuid primary key default gen_random_uuid(),
    product_id            text not null,
    product_snapshot_hash text not null,
    embedding_model       text not null,
    embedding_text        text not null,
    embedding             vector(1536) not null,
    metadata              jsonb not null default '{}',
    created_at            timestamptz not null default now(),
    updated_at            timestamptz not null default now(),

    constraint product_catalog_embeddings_unique_snapshot
        unique (product_id, product_snapshot_hash, embedding_model)
);

create index if not exists product_embeddings_product_idx
    on product_catalog_embeddings (product_id, updated_at desc);

alter table invoice_import_runs enable row level security;
alter table product_embedding_sync enable row level security;
alter table product_catalog_embeddings enable row level security;

create or replace function create_or_reuse_product_sync_row(
    product_id text,
    product_snapshot_hash text,
    embedding_model text,
    name text,
    barcode text,
    category text,
    uom text,
    supplier text,
    price_eur numeric,
    price_50 numeric,
    price_70 numeric,
    price_100 numeric,
    markup integer,
    source_import_id text,
    source_row_id text,
    invoice_number text,
    sync_status text,
    attempt_count integer,
    last_error text default null,
    claimed_at timestamptz default null,
    claimed_by text default null,
    next_retry_at timestamptz default null,
    last_synced_at timestamptz default null
) returns table (
    id uuid,
    product_id text,
    product_snapshot_hash text,
    embedding_model text,
    name text,
    barcode text,
    category text,
    uom text,
    supplier text,
    price_eur numeric,
    price_50 numeric,
    price_70 numeric,
    price_100 numeric,
    markup integer,
    source_import_id text,
    source_row_id text,
    invoice_number text,
    sync_status text,
    attempt_count integer,
    last_error text,
    claimed_at timestamptz,
    claimed_by text,
    next_retry_at timestamptz,
    last_synced_at timestamptz,
    created_at timestamptz,
    updated_at timestamptz,
    created boolean
) language plpgsql security definer
set search_path = public
as $$
declare
    inserted_row product_embedding_sync%rowtype;
    existing_row product_embedding_sync%rowtype;
begin
    insert into product_embedding_sync (
        product_id,
        product_snapshot_hash,
        embedding_model,
        name,
        barcode,
        category,
        uom,
        supplier,
        price_eur,
        price_50,
        price_70,
        price_100,
        markup,
        source_import_id,
        source_row_id,
        invoice_number,
        sync_status,
        attempt_count,
        last_error,
        claimed_at,
        claimed_by,
        next_retry_at,
        last_synced_at
    ) values (
        create_or_reuse_product_sync_row.product_id,
        create_or_reuse_product_sync_row.product_snapshot_hash,
        create_or_reuse_product_sync_row.embedding_model,
        create_or_reuse_product_sync_row.name,
        create_or_reuse_product_sync_row.barcode,
        create_or_reuse_product_sync_row.category,
        create_or_reuse_product_sync_row.uom,
        create_or_reuse_product_sync_row.supplier,
        create_or_reuse_product_sync_row.price_eur,
        create_or_reuse_product_sync_row.price_50,
        create_or_reuse_product_sync_row.price_70,
        create_or_reuse_product_sync_row.price_100,
        create_or_reuse_product_sync_row.markup,
        create_or_reuse_product_sync_row.source_import_id,
        create_or_reuse_product_sync_row.source_row_id,
        create_or_reuse_product_sync_row.invoice_number,
        create_or_reuse_product_sync_row.sync_status,
        create_or_reuse_product_sync_row.attempt_count,
        create_or_reuse_product_sync_row.last_error,
        create_or_reuse_product_sync_row.claimed_at,
        create_or_reuse_product_sync_row.claimed_by,
        create_or_reuse_product_sync_row.next_retry_at,
        create_or_reuse_product_sync_row.last_synced_at
    )
    on conflict (product_id, product_snapshot_hash) do nothing
    returning * into inserted_row;

    if found then
        return query
        select
            inserted_row.id,
            inserted_row.product_id,
            inserted_row.product_snapshot_hash,
            inserted_row.embedding_model,
            inserted_row.name,
            inserted_row.barcode,
            inserted_row.category,
            inserted_row.uom,
            inserted_row.supplier,
            inserted_row.price_eur,
            inserted_row.price_50,
            inserted_row.price_70,
            inserted_row.price_100,
            inserted_row.markup,
            inserted_row.source_import_id,
            inserted_row.source_row_id,
            inserted_row.invoice_number,
            inserted_row.sync_status,
            inserted_row.attempt_count,
            inserted_row.last_error,
            inserted_row.claimed_at,
            inserted_row.claimed_by,
            inserted_row.next_retry_at,
            inserted_row.last_synced_at,
            inserted_row.created_at,
            inserted_row.updated_at,
            true;
        return;
    end if;

    select * into existing_row
    from product_embedding_sync
    where product_embedding_sync.product_id = create_or_reuse_product_sync_row.product_id
      and product_embedding_sync.product_snapshot_hash = create_or_reuse_product_sync_row.product_snapshot_hash
    limit 1;

    return query
    select
        existing_row.id,
        existing_row.product_id,
        existing_row.product_snapshot_hash,
        existing_row.embedding_model,
        existing_row.name,
        existing_row.barcode,
        existing_row.category,
        existing_row.uom,
        existing_row.supplier,
        existing_row.price_eur,
        existing_row.price_50,
        existing_row.price_70,
        existing_row.price_100,
        existing_row.markup,
        existing_row.source_import_id,
        existing_row.source_row_id,
        existing_row.invoice_number,
        existing_row.sync_status,
        existing_row.attempt_count,
        existing_row.last_error,
        existing_row.claimed_at,
        existing_row.claimed_by,
        existing_row.next_retry_at,
        existing_row.last_synced_at,
        existing_row.created_at,
        existing_row.updated_at,
        false;
end;
$$;

create or replace function claim_next_product_sync_row(
    p_worker_id text,
    p_now timestamptz,
    p_lease_timeout_seconds integer
) returns setof product_embedding_sync
language sql security definer
set search_path = public
as $$
    with candidate as (
        select id
        from product_embedding_sync
        where
            sync_status = 'pending'
            or (sync_status = 'failed' and (next_retry_at is null or next_retry_at <= p_now))
            or (
                sync_status = 'processing'
                and claimed_at is not null
                and claimed_at <= p_now - make_interval(secs => p_lease_timeout_seconds)
            )
        order by created_at
        limit 1
        for update skip locked
    )
    update product_embedding_sync pes
    set
        sync_status = 'processing',
        claimed_at = p_now,
        claimed_by = p_worker_id,
        updated_at = p_now
    from candidate
    where pes.id = candidate.id
    returning pes.*;
$$;

create or replace function match_product_catalog_embeddings(
    p_query_embedding vector(1536),
    p_embedding_model text,
    p_match_count integer default 5
) returns table (
    product_id text,
    product_snapshot_hash text,
    embedding_model text,
    embedding_text text,
    metadata jsonb,
    score double precision
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
