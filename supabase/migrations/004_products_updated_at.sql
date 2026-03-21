-- Add updated_at to existing products table.

do $$
begin
    if not exists (
        select 1 from information_schema.columns
        where table_name = 'products' and column_name = 'updated_at'
    ) then
        alter table products add column updated_at timestamptz not null default now();
    end if;
end $$;
