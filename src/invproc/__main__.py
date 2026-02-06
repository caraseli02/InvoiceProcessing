"""CLI entry point for invoice processing."""

import typer

from invproc.cli import app as cli_app
from invproc.config import get_config

app = typer.Typer(
    help="Invoice processing tool - CLI or API mode.",
    no_args_is_help=False,
)
app.add_typer(cli_app, name="", help="Invoice processing CLI commands.")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    mode: str = typer.Option(
        "cli",
        "--mode",
        help="Run mode: cli (default) or api",
    ),
) -> None:
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
        raise typer.Exit()

    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


if __name__ == "__main__":
    app()
