"""CLI entry point for invoice processing."""

import typer

from invproc.cli import app as cli_app
from invproc.config import get_config


def main(
    mode: str = typer.Option(
        "cli",
        "--mode",
        help="Run mode: cli (default) or api",
    ),
):
    """Invoice processing tool - CLI or API mode."""
    if mode == "api":
        import uvicorn

        config = get_config()
        uvicorn.run(
            "invproc.api:app",
            host=config.api_host,
            port=config.api_port,
            reload=False,
        )
    else:
        cli_app()


if __name__ == "__main__":
    cli_app()
