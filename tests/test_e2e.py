"""End-to-end tests for CLI and API consistency."""

import os
import json
import pytest
from typer.testing import CliRunner
from fastapi.testclient import TestClient

from invproc.cli import app
from invproc.api import app as api_app, load_api_keys
from invproc.config import reload_config

cli_runner = CliRunner()


@pytest.mark.e2e
def test_cli_api_consistency():
    """Test that CLI and API produce identical results."""
    original_api_keys = os.environ.get("API_KEYS")
    original_mock = os.environ.get("MOCK")

    try:
        os.environ["API_KEYS"] = "test-api-key"
        os.environ["MOCK"] = "true"
        reload_config()
        load_api_keys()

        with TestClient(api_app) as api_client:
            with open("test_invoices/invoice-test.pdf", "rb") as f:
                api_response = api_client.post(
                    "/extract",
                    files={"file": ("test.pdf", f, "application/pdf")},
                    headers={"X-API-Key": "test-api-key"},
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
        if original_api_keys is not None:
            os.environ["API_KEYS"] = original_api_keys
        elif "API_KEYS" in os.environ:
            del os.environ["API_KEYS"]

        if original_mock is not None:
            os.environ["MOCK"] = original_mock
        elif "MOCK" in os.environ:
            del os.environ["MOCK"]
