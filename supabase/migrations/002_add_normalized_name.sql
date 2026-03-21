-- Patch: add normalized_name to existing products table
-- Safe to run multiple times (checks column existence first).

do $$
begin
    if not exists (
        select 1 from information_schema.columns
        where table_name = 'products' and column_name = 'normalized_name'
    ) then
        alter table products add column normalized_name text not null default '';
        create index if not exists products_normalized_name_idx on products (normalized_name);
    end if;
end $$;

-- Back-fill any existing rows from the name column.
update products set normalized_name = lower(regexp_replace(trim(name), '\s+', ' ', 'g'))
where normalized_name = '';

alter table products enable row level security;
