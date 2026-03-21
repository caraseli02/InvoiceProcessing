-- Change stock_movements.quantity from integer to numeric to support fractional quantities.
alter table stock_movements alter column quantity type numeric using quantity::numeric;
