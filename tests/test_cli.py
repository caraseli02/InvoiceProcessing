"""Test suite for invoice processing CLI."""

import json
from pathlib import Path
from typer.testing import CliRunner

from invproc.cli import app

runner = CliRunner()


def test_cli_process_basic():
    """Test basic CLI process command with mock."""
    result = runner.invoke(app, ["process", "test_invoices/invoice-test.pdf", "--mock"])
    assert result.exit_code == 0
    output = result.output.strip()
    json_start = output.find("{")
    json_end = output.rfind("}") + 1
    json_data = output[json_start:json_end]
    data = json.loads(json_data)
    assert "supplier" in data
    assert "products" in data


def test_cli_process_debug():
    """Test CLI process with debug flag."""
    result = runner.invoke(
        app, ["process", "test_invoices/invoice-test.pdf", "--mock", "--debug"]
    )
    assert result.exit_code == 0
    grid_file = Path("output/grids/invoice-test_grid.txt")
    assert grid_file.exists()
    grid_file.unlink()


def test_cli_process_with_output():
    """Test CLI process with output file."""
    output_file = Path("/tmp/test_output.json")
    result = runner.invoke(
        app,
        [
            "process",
            "test_invoices/invoice-test.pdf",
            "--mock",
            "--output",
            str(output_file),
        ],
    )
    assert result.exit_code == 0
    assert output_file.exists()
    data = json.loads(output_file.read_text())
    assert "supplier" in data
    output_file.unlink()


def test_cli_process_invalid_file():
    """Test CLI process with non-existent file."""
    result = runner.invoke(app, ["process", "nonexistent.pdf", "--mock"])
    assert result.exit_code != 0
    assert "does not exist" in result.output.lower()


def test_cli_version():
    """Test version command."""
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "invproc version 0.1.0" in result.output


def test_cli_process_verbose():
    """Test CLI process with verbose logging."""
    result = runner.invoke(
        app, ["process", "test_invoices/invoice-test.pdf", "--mock", "--verbose"]
    )
    assert result.exit_code == 0
    assert "Configuration:" in result.output
