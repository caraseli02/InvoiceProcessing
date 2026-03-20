"""CLI interface for invoice processing."""

import logging
import json
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import track

from .config import get_config_unvalidated, reload_config, InvoiceConfig
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
_CLI_RAG_RESOURCES_KEY: tuple[bool, str] | None = None


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
                import json

                print(json.dumps(results[-1].model_dump(mode="json"), indent=2))

        else:
            result = _extract_single(input_file, config, debug, verbose, mock)
            if output_file:
                _save_output(result, output_file)
            else:
                import json

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


def _get_cli_rag_resources(*, mock: bool) -> AppResources:
    global _CLI_RAG_RESOURCES
    global _CLI_RAG_RESOURCES_KEY

    config = get_config_unvalidated()
    config.mock = mock
    config.validate_config()
    cache_key = (config.mock, config.catalog_sync_embedding_model)

    if _CLI_RAG_RESOURCES is None or _CLI_RAG_RESOURCES_KEY != cache_key:
        _CLI_RAG_RESOURCES = build_app_resources(config)
        _CLI_RAG_RESOURCES_KEY = cache_key

    return _CLI_RAG_RESOURCES


def _build_rag_services(
    *,
    mock: bool,
) -> tuple[AppResources, CatalogSyncWorker, CatalogRetrievalService]:
    resources = _get_cli_rag_resources(mock=mock)
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
                "results": [result.__dict__ for result in results],
            },
            indent=2,
        )
    )


@rag_app.command("query")
def rag_query(
    text: str = typer.Argument(..., help="Semantic catalog query."),
    top_k: int = typer.Option(5, "--top-k", min=1, help="Number of matches to return."),
    mock: bool = typer.Option(
        False,
        "--mock",
        help="Use deterministic offline embeddings instead of OpenAI API calls.",
    ),
) -> None:
    """Query backend-owned catalog embeddings."""
    _, _, retrieval_service = _build_rag_services(mock=mock)
    result = retrieval_service.query(text, top_k=top_k)
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
) -> None:
    """Evaluate retrieval quality with representative WhatsApp-style queries."""
    _, _, retrieval_service = _build_rag_services(mock=mock)
    evaluator = CatalogRagEvaluator(retrieval_service)
    result = evaluator.evaluate(load_eval_cases(fixture_path))
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
