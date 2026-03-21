-- Add missing columns to existing stock_movements table.

do $$
begin
    if not exists (
        select 1 from information_schema.columns
        where table_name = 'stock_movements' and column_name = 'source'
    ) then
        alter table stock_movements add column source text;
    end if;

    if not exists (
        select 1 from information_schema.columns
        where table_name = 'stock_movements' and column_name = 'invoice_number'
    ) then
        alter table stock_movements add column invoice_number text;
    end if;
end $$;

alter table stock_movements enable row level security;
