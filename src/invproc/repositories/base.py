"""Repository interfaces for invoice import persistence."""

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass(frozen=True)
class ProductRecord:
    """Persisted product shape used by import service."""

    product_id: str
    barcode: Optional[str]
    name: str
    normalized_name: str
    supplier: Optional[str]


@dataclass(frozen=True)
class UpsertProductInput:
    """Input payload for create/update product operations."""

    name: str
    barcode: Optional[str]
    supplier: Optional[str]
    price: float
    price_50: float
    price_70: float
    price_100: float
    markup: int


class InvoiceImportRepository(Protocol):
    """Persistence operations required for invoice import."""

    def find_product_by_barcode(self, barcode: str) -> Optional[ProductRecord]:
        ...

    def find_products_by_normalized_name(self, normalized_name: str) -> list[ProductRecord]:
        ...

    def create_product(self, data: UpsertProductInput) -> ProductRecord:
        ...

    def update_product(self, product_id: str, data: UpsertProductInput) -> ProductRecord:
        ...

    def add_stock_movement_in(
        self,
        *,
        product_id: str,
        quantity: float,
        source: str,
        invoice_number: Optional[str],
    ) -> str:
        ...

    def get_idempotent_result(self, idempotency_key: str) -> Optional[tuple[str, dict]]:
        ...

    def save_idempotent_result(
        self, *, idempotency_key: str, request_hash: str, response_payload: dict
    ) -> None:
        ...
