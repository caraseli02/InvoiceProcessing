"""In-memory repository for MVP import flow and tests."""

from __future__ import annotations

import threading
from typing import Optional

from invproc.repositories.base import InvoiceImportRepository, ProductRecord, UpsertProductInput


class InMemoryInvoiceImportRepository(InvoiceImportRepository):
    """Thread-safe in-memory storage."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        """Reset all in-memory state (used by tests)."""
        with self._lock:
            self._products: dict[str, ProductRecord] = {}
            self._products_by_barcode: dict[str, str] = {}
            self._movements: dict[str, dict] = {}
            self._idempotency: dict[str, tuple[str, dict]] = {}
            self._product_seq = 1
            self._movement_seq = 1

    def find_product_by_barcode(self, barcode: str) -> Optional[ProductRecord]:
        with self._lock:
            product_id = self._products_by_barcode.get(barcode)
            if not product_id:
                return None
            return self._products.get(product_id)

    def find_products_by_normalized_name(self, normalized_name: str) -> list[ProductRecord]:
        with self._lock:
            return [
                p for p in self._products.values() if p.normalized_name == normalized_name
            ]

    def create_product(self, data: UpsertProductInput) -> ProductRecord:
        from invproc.import_service import normalize_name

        with self._lock:
            product_id = f"prod_{self._product_seq}"
            self._product_seq += 1
            product = ProductRecord(
                product_id=product_id,
                barcode=data.barcode,
                name=data.name,
                normalized_name=normalize_name(data.name),
                supplier=data.supplier,
            )
            self._products[product_id] = product
            if data.barcode:
                self._products_by_barcode[data.barcode] = product_id
            return product

    def update_product(self, product_id: str, data: UpsertProductInput) -> ProductRecord:
        from invproc.import_service import normalize_name

        with self._lock:
            if product_id not in self._products:
                raise KeyError(f"Unknown product_id: {product_id}")

            product = ProductRecord(
                product_id=product_id,
                barcode=data.barcode,
                name=data.name,
                normalized_name=normalize_name(data.name),
                supplier=data.supplier,
            )
            self._products[product_id] = product
            if data.barcode:
                self._products_by_barcode[data.barcode] = product_id
            return product

    def add_stock_movement_in(
        self,
        *,
        product_id: str,
        quantity: float,
        source: str,
        invoice_number: Optional[str],
    ) -> str:
        with self._lock:
            movement_id = f"mov_{self._movement_seq}"
            self._movement_seq += 1
            self._movements[movement_id] = {
                "product_id": product_id,
                "quantity": quantity,
                "source": source,
                "invoice_number": invoice_number,
                "type": "IN",
            }
            return movement_id

    def get_idempotent_result(self, idempotency_key: str) -> Optional[tuple[str, dict]]:
        with self._lock:
            return self._idempotency.get(idempotency_key)

    def save_idempotent_result(
        self, *, idempotency_key: str, request_hash: str, response_payload: dict
    ) -> None:
        with self._lock:
            self._idempotency[idempotency_key] = (request_hash, response_payload)
