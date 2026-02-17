"""Tests for invproc.__main__ CLI entrypoint behavior."""

import sys
from types import SimpleNamespace

from typer.testing import CliRunner

from invproc import __main__ as main_module


runner = CliRunner()


def test_main_shows_help_when_no_subcommand():
    """Default CLI mode should show help when no subcommand is provided."""
    result = runner.invoke(main_module.app, [])
    assert result.exit_code == 0
    assert "Invoice processing tool - CLI or API mode." in result.output


def test_main_api_mode_runs_uvicorn(monkeypatch):
    """API mode should launch uvicorn with config host/port."""
    calls: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs

    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=fake_run))
    monkeypatch.setattr(
        main_module,
        "get_config",
        lambda: SimpleNamespace(api_host="127.0.0.1", api_port=9001),
    )

    result = runner.invoke(main_module.app, ["--mode", "api"])

    assert result.exit_code == 0
    assert calls["args"] == ("invproc.api:app",)
    assert calls["kwargs"] == {
        "host": "127.0.0.1",
        "port": 9001,
        "reload": False,
    }


def test_main_unknown_mode_falls_back_to_help():
    """Unknown mode currently falls back to CLI help output."""
    result = runner.invoke(main_module.app, ["--mode", "not-a-mode"])
    assert result.exit_code == 0
    assert "Invoice processing tool - CLI or API mode." in result.output
