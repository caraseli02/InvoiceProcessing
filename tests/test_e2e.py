"""End-to-end tests for CLI and API consistency."""

import os
import json
import pytest
from typer.testing import CliRunner
from fastapi.testclient import TestClient

from invproc.cli import app
from invproc.api import app as api_app
from invproc.config import reload_config

cli_runner = CliRunner()


@pytest.mark.e2e
def test_cli_api_consistency():
    """Test that CLI and API produce identical results."""
    original_mock = os.environ.get("MOCK")

    try:
        os.environ["ALLOWED_ORIGINS"] = "http://localhost:5173"
        os.environ["MOCK"] = "true"
        reload_config()

        with TestClient(api_app) as api_client:
            with open("test_invoices/invoice-test.pdf", "rb") as f:
                api_response = api_client.post(
                    "/extract",
                    files={"file": ("test.pdf", f, "application/pdf")},
                    headers={"Authorization": "Bearer test-supabase-jwt"},
                )

        cli_result = cli_runner.invoke(
            app, ["process", "test_invoices/invoice-test.pdf", "--mock"]
        )

        api_data = api_response.json()
        output = cli_result.output.strip()
        json_start = output.find("{")
        json_end = output.rfind("}") + 1
        json_data = output[json_start:json_end]
        cli_data = json.loads(json_data)

        assert api_data["total_amount"] == cli_data["total_amount"]
        assert api_data["currency"] == cli_data["currency"]
        assert len(api_data["products"]) == len(cli_data["products"])
    finally:
        if original_mock is not None:
            os.environ["MOCK"] = original_mock
        elif "MOCK" in os.environ:
            del os.environ["MOCK"]

        reload_config()
