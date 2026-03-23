"""CLI interface for invoice processing."""

import dataclasses
import json
import logging
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import track

from .config import get_config_unvalidated, reload_config, InvoiceConfig
from .import_service import InvoiceImportService
from .models import InvoiceImportRequest
from .pdf_processor import PDFProcessor
from .llm_extractor import LLMExtractor
from .api import build_app_resources
from .dependencies import AppResources
from .rag import (
    CatalogRagEvaluator,
    CatalogRetrievalService,
    CatalogSyncWorker,
    build_rag_worker,
    build_retrieval_service,
    build_sync_status_snapshot,
    load_eval_cases,
    serialize_eval_result,
    serialize_mode_comparison,
    serialize_query_result,
    serialize_sync_status_snapshot,
)
from .validator import InvoiceValidator
from .models import InvoiceData

app = typer.Typer(
    name="invproc",
    help="""
    [bold]Invoice Processing CLI[/bold]

    Extract structured data from invoice PDFs using AI and OCR.

    [cyan]Examples:[/cyan]
      invproc process invoice.pdf
      invproc process *.pdf --output ./results
      invproc process invoice.pdf --format json --verbose

    [cyan]Getting Started:[/cyan]
      1. Set your OpenAI API key: export INVPROC_OPENAI_API_KEY=sk-...
      2. Process an invoice: invproc process invoice.pdf
      3. View help: invproc --help

    For more information: https://github.com/yourusername/invproc
    """,
    no_args_is_help=True,
)
rag_app = typer.Typer(help="Backend-owned RAG sync, query, and evaluation commands.")
app.add_typer(rag_app, name="rag")

console = Console()
logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
_CLI_RAG_RESOURCES: AppResources | None = None
_CLI_RAG_RESOURCES_KEY: tuple[bool, str, bool, str] | None = None


@app.command()
def process(
    input_file: Path = typer.Argument(
        ...,
        help="Invoice PDF file to process",
        exists=True,
    ),
    output_file: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output JSON file (default: stdout)",
        resolve_path=True,
    ),
    lang: Optional[str] = typer.Option(
        None,
        "--lang",
        help="OCR language codes (e.g., ron+eng+rus)",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Enable debug mode (save text grids to output/grids/)",
    ),
    retry: Optional[int] = typer.Option(
        None,
        "--retry",
        help="Run extraction N times, compare results for consistency",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed processing information",
    ),
    mock: bool = typer.Option(
        False,
        "--mock",
        help="Use mock data instead of calling OpenAI API (for testing without API key)",
    ),
) -> None:
    """Process invoice file and extract structured data."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    config = get_config_unvalidated()
    config.mock = mock

    if output_file:
        config.output_dir = output_file.parent
        config.output_dir.mkdir(parents=True, exist_ok=True)

    if lang:
        config.ocr_languages = lang
    config.validate_config()

    file_size = input_file.stat().st_size
    if file_size > MAX_FILE_SIZE:
        console.print(
            f"\n[bold red]✗ Error:[/bold red] File too large: {file_size:,} bytes (max {MAX_FILE_SIZE:,} = 50 MB)"
        )
        raise typer.Exit(code=1)

    if verbose:
        console.print("[bold]Configuration:[/bold]")
        console.print(f"  Model: {config.model}")
        console.print(f"  Scale factor: {config.scale_factor}")
        console.print(f"  Tolerance: {config.tolerance}px")
        console.print(f"  OCR languages: {config.ocr_languages}")
        console.print(f"  Temperature: {config.temperature}")
        console.print(f"  Mock mode: {config.mock}")
        console.print()

    if mock:
        console.print(
            "[yellow]⚠️  Using mock mode - no OpenAI API calls will be made[/yellow]\n"
        )

    start_time = time.time()

    try:
        if retry:
            results = []
            console.print(
                f"Running extraction [bold]{retry}[/bold] times for consistency check...\n"
            )

            for i in track(range(retry), description="Processing"):
                reload_config()
                if lang:
                    config.ocr_languages = lang

                result = _extract_single(input_file, config, debug, verbose, mock)
                results.append(result)

            _check_consistency(results)
            if output_file:
                _save_output(results[-1], output_file)
            else:
                print(json.dumps(results[-1].model_dump(mode="json"), indent=2))

        else:
            result = _extract_single(input_file, config, debug, verbose, mock)
            if output_file:
                _save_output(result, output_file)
            else:
                print(json.dumps(result.model_dump(mode="json"), indent=2))

            elapsed = time.time() - start_time
            console.print(
                f"\n[bold green]✓ Processed successfully![/bold green] ({elapsed:.1f}s)"
            )

    except Exception as e:
        console.print(f"\n[bold red]✗ Error:[/bold red] {str(e)}")
        if verbose:
            import traceback

            console.print(f"[dim white]{traceback.format_exc()}[/dim white]")
        raise typer.Exit(code=1)


def _extract_single(
    input_file: Path,
    config: InvoiceConfig,
    debug: bool,
    verbose: bool,
    mock: bool,
) -> InvoiceData:
    """Extract invoice data once."""
    if verbose:
        console.print(f"Processing: [bold blue]{input_file}[/bold blue]")

    pdf_processor = PDFProcessor(config)
    text_grid, metadata = pdf_processor.extract_content(input_file, debug)

    if debug:
        grid_file = config.output_dir / "grids" / f"{input_file.stem}_grid.txt"
        grid_file.parent.mkdir(parents=True, exist_ok=True)
        grid_file.write_text(text_grid)
        console.print(f"[dim]Saved text grid to {grid_file}[/dim]")

    llm_extractor = LLMExtractor(config=config)
    invoice_data = llm_extractor.parse_with_llm(text_grid)

    validator = InvoiceValidator(config)
    invoice_data = validator.validate_invoice(invoice_data)

    return invoice_data


def _save_output(invoice_data: InvoiceData, output_file: Path) -> None:
    """Save invoice data to JSON file."""
    with open(output_file, "w") as f:
        json.dump(invoice_data.model_dump(mode="json"), f, indent=2)
    console.print(f"[dim]Saved output to {output_file}[/dim]")


def _check_consistency(results: list[InvoiceData]) -> None:
    """Check if all runs produced identical results."""
    if len(results) <= 1:
        return

    first_result = results[0].model_dump(mode="json")
    all_equal = all(r.model_dump(mode="json") == first_result for r in results[1:])

    if all_equal:
        console.print(
            f"\n[bold green]✓ All {len(results)} runs produced identical results[/bold green]"
        )
    else:
        console.print(
            "\n[bold yellow]⚠️  INCONSISTENT RESULTS - manual review needed[/bold yellow]"
        )
        console.print("[dim]Runs produced different outputs[/dim]")


@app.command()
def version() -> None:
    """Show version information."""
    console.print("invproc version 0.1.0")


def _build_default_idempotency_key(payload: InvoiceImportRequest) -> str:
    """Derive a deterministic idempotency key from the effective import payload."""
    encoded = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    import hashlib

    return f"cli:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _build_import_request_from_invoice(
    invoice_data: InvoiceData,
    *,
    default_weight_kg: float | None = None,
) -> InvoiceImportRequest:
    """Map extracted invoice output into the import request contract."""
    if not invoice_data.products:
        raise ValueError("Extracted invoice does not contain any products to import")

    return InvoiceImportRequest.model_validate(
        {
            "invoice_meta": {
                "supplier": invoice_data.supplier,
                "invoice_number": invoice_data.invoice_number,
                "date": invoice_data.date,
            },
            "rows": [
                {
                    "row_id": product.row_id or f"row-{index}",
                    "name": product.name,
                    "barcode": product.raw_code,
                    "quantity": product.quantity,
                    "line_total_lei": product.total_price,
                    "weight_kg": product.weight_kg_candidate
                    if product.weight_kg_candidate is not None
                    else default_weight_kg,
                    "category": product.category_suggestion
                    if product.category_suggestion != "General"
                    else None,
                    "uom": product.uom,
                }
                for index, product in enumerate(invoice_data.products, start=1)
            ],
        }
    )


def _build_import_service(resources: AppResources) -> InvoiceImportService:
    """Build an import service bound to the app-owned CLI resources."""
    return InvoiceImportService(
        config=resources.config,
        repository=resources.import_repository,
        catalog_sync_producer=resources.catalog_sync_producer,
    )


def _get_cli_rag_resources(*, mock: bool, enable_catalog_sync: bool = False) -> AppResources:
    global _CLI_RAG_RESOURCES
    global _CLI_RAG_RESOURCES_KEY

    base_config = get_config_unvalidated()
    config = base_config.model_copy(
        update={
            "mock": mock,
            "catalog_sync_enabled": base_config.catalog_sync_enabled or enable_catalog_sync,
        }
    )
    config.validate_config()
    cache_key = (
        config.mock,
        config.catalog_sync_embedding_model,
        config.catalog_sync_enabled,
        config.import_repository_backend,
    )

    if _CLI_RAG_RESOURCES is None or _CLI_RAG_RESOURCES_KEY != cache_key:
        _CLI_RAG_RESOURCES = build_app_resources(config)
        _CLI_RAG_RESOURCES_KEY = cache_key

    return _CLI_RAG_RESOURCES


def _build_rag_services(
    *,
    mock: bool,
    enable_catalog_sync: bool = False,
) -> tuple[AppResources, CatalogSyncWorker, CatalogRetrievalService]:
    resources = _get_cli_rag_resources(
        mock=mock,
        enable_catalog_sync=enable_catalog_sync,
    )
    worker = build_rag_worker(
        repository=resources.import_repository,
        config=resources.config,
        worker_id="cli",
    )
    retrieval_service = build_retrieval_service(
        repository=resources.import_repository,
        config=resources.config,
    )
    return resources, worker, retrieval_service


@rag_app.command("ingest-invoice")
def rag_ingest_invoice(
    input_file: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        resolve_path=True,
        help="Invoice PDF file to extract, import, and optionally sync.",
    ),
    idempotency_key: Optional[str] = typer.Option(
        None,
        "--idempotency-key",
        help="Override the default deterministic CLI idempotency key.",
    ),
    sync: bool = typer.Option(
        True,
        "--sync/--no-sync",
        help="Process catalog sync rows after import in the same invocation.",
    ),
    limit: int = typer.Option(
        100,
        "--limit",
        min=1,
        help="Maximum catalog sync rows to process when --sync is enabled.",
    ),
    default_weight_kg: Optional[float] = typer.Option(
        None,
        "--default-weight-kg",
        min=0.000001,
        help="Fallback weight to use for extracted rows that are missing weight_kg_candidate.",
    ),
    query: Optional[str] = typer.Option(
        None,
        "--query",
        help="Optional semantic query to run after sync for end-to-end validation.",
    ),
    top_k: int = typer.Option(
        5,
        "--top-k",
        min=1,
        help="Number of semantic matches to return when --query is used.",
    ),
    query_mode: str = typer.Option(
        "hybrid",
        "--query-mode",
        help="Search mode for --query: semantic | lexical | hybrid (default: hybrid).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print the full machine-readable payload instead of the default summary.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show extraction configuration details before ingestion.",
    ),
    mock: bool = typer.Option(
        False,
        "--mock",
        help="Use deterministic mock extraction/embedding behavior for testing.",
    ),
) -> None:
    """Run extract -> import -> sync for one invoice PDF using app-owned resources."""
    if query is not None and not sync:
        raise typer.BadParameter("--query requires --sync so vectors exist before retrieval")

    resources, worker, retrieval_service = _build_rag_services(
        mock=mock,
        enable_catalog_sync=True,
    )
    invoice_data = _extract_single(
        input_file,
        resources.config,
        debug=False,
        verbose=verbose,
        mock=mock,
    )
    missing_weight_count = sum(
        1 for product in invoice_data.products if product.weight_kg_candidate is None
    )
    import_request = _build_import_request_from_invoice(
        invoice_data,
        default_weight_kg=default_weight_kg,
    )
    service = _build_import_service(resources)
    resolved_idempotency_key = idempotency_key or _build_default_idempotency_key(import_request)
    import_response = service.import_rows(
        import_request,
        idempotency_key=resolved_idempotency_key,
    )

    payload: dict[str, object] = {
        "invoice": {
            "supplier": invoice_data.supplier,
            "invoice_number": invoice_data.invoice_number,
            "date": invoice_data.date,
            "currency": invoice_data.currency,
            "product_count": len(invoice_data.products),
            "missing_weight_count": missing_weight_count,
        },
        "idempotency_key": resolved_idempotency_key,
        "default_weight_kg": default_weight_kg,
        "import": import_response.model_dump(mode="json"),
    }
    sync_payload: dict[str, object] | None = None
    query_payload: dict[str, object] | None = None

    if sync:
        results = worker.sync_pending(limit=limit)
        sync_payload = {
            "processed": len(results),
            "results": [dataclasses.asdict(result) for result in results],
        }
        payload["sync"] = sync_payload

    if query is not None:
        query_payload = serialize_query_result(
            retrieval_service.query(query, top_k=top_k, mode=query_mode)  # type: ignore[arg-type]
        )
        payload["query"] = query_payload

    if json_output:
        print(json.dumps(payload, indent=2))
        return

    summary: dict[str, object] = {
        "invoice_number": invoice_data.invoice_number,
        "product_count": len(invoice_data.products),
        "missing_weight_count": missing_weight_count,
        "import_status": import_response.import_status,
        "created_count": import_response.summary.created_count,
        "updated_count": import_response.summary.updated_count,
        "error_count": import_response.summary.error_count,
    }
    if sync_payload is not None:
        summary["synced_count"] = sync_payload["processed"]
    if query_payload is not None:
        query_matches = query_payload.get("matches")
        if not isinstance(query_matches, list):
            raise RuntimeError("serialize_query_result returned a non-list matches payload")
        summary["top_match_product_ids"] = [
            str(match["product_id"])
            for match in query_matches
            if isinstance(match, dict) and "product_id" in match
        ]
    print(json.dumps(summary, indent=2))


@rag_app.command("sync-pending")
def rag_sync_pending(
    limit: int = typer.Option(100, "--limit", min=1, help="Maximum rows to process."),
    mock: bool = typer.Option(
        False,
        "--mock",
        help="Use deterministic offline embeddings instead of OpenAI API calls.",
    ),
) -> None:
    """Process pending or retry-due catalog sync rows."""
    _, worker, _ = _build_rag_services(mock=mock)
    results = worker.sync_pending(limit=limit)
    print(
        json.dumps(
            {
                "processed": len(results),
                "results": [dataclasses.asdict(result) for result in results],
            },
            indent=2,
        )
    )


@rag_app.command("query")
def rag_query(
    text: str = typer.Argument(..., help="Semantic catalog query."),
    top_k: int = typer.Option(5, "--top-k", min=1, help="Number of matches to return."),
    mode: str = typer.Option(
        "hybrid",
        "--mode",
        help="Search mode: semantic | lexical | hybrid (default: hybrid).",
    ),
    mock: bool = typer.Option(
        False,
        "--mock",
        help="Use deterministic offline embeddings instead of OpenAI API calls.",
    ),
) -> None:
    """Query backend-owned catalog embeddings."""
    _, _, retrieval_service = _build_rag_services(mock=mock)
    result = retrieval_service.query(text, top_k=top_k, mode=mode)  # type: ignore[arg-type]
    print(json.dumps(serialize_query_result(result), indent=2))


@rag_app.command("eval")
def rag_eval(
    fixture_path: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        resolve_path=True,
        help="JSON fixture containing representative RAG queries.",
    ),
    mock: bool = typer.Option(
        False,
        "--mock",
        help="Use deterministic offline embeddings instead of OpenAI API calls.",
    ),
    mode: str = typer.Option(
        "hybrid",
        "--mode",
        help="Search mode to evaluate: semantic, lexical, or hybrid.",
    ),
    all_modes: bool = typer.Option(
        False,
        "--all-modes",
        help="Run eval for all three search modes and print a side-by-side comparison.",
    ),
    top_k: int = typer.Option(
        10,
        "--top-k",
        min=1,
        max=50,
        help="Number of results to retrieve per query.",
    ),
) -> None:
    """Evaluate retrieval quality with representative WhatsApp-style queries."""
    if mode not in ("semantic", "lexical", "hybrid"):
        typer.echo(f"Invalid mode '{mode}'. Choose: semantic, lexical, hybrid.", err=True)
        raise typer.Exit(code=1)
    _, _, retrieval_service = _build_rag_services(mock=mock)
    evaluator = CatalogRagEvaluator(retrieval_service)
    cases = load_eval_cases(fixture_path)
    if all_modes:
        comparison = evaluator.evaluate_all_modes(cases, top_k=top_k)
        print(json.dumps(serialize_mode_comparison(comparison), indent=2))
    else:
        result = evaluator.evaluate(cases, mode=mode, top_k=top_k)  # type: ignore[arg-type]
        print(json.dumps(serialize_eval_result(result), indent=2))


@rag_app.command("status")
def rag_status(
    mock: bool = typer.Option(
        False,
        "--mock",
        help="Use the mock-configured CLI app resources.",
    ),
) -> None:
    """Show backend sync queue status for operational validation."""
    resources = _get_cli_rag_resources(mock=mock)
    snapshot = build_sync_status_snapshot(resources.import_repository)
    print(json.dumps(serialize_sync_status_snapshot(snapshot), indent=2))


if __name__ == "__main__":
    app()
