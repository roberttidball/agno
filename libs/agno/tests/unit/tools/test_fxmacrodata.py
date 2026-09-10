"""Native Agno registration and transport-boundary tests."""

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest
from agno.agent import Agent
from agno.tools.function import Function, FunctionCall

pytest.importorskip("fxmacrodata_public")

from agno.tools.fxmacrodata import FXMacroDataTools  # noqa: E402
from fxmacrodata_public import Result, list_operations  # noqa: E402

PAYLOAD = {"data": [{"fixture": "synthetic", "announcement_datetime": "2026-01-01T12:00:00Z", "value": None}]}


@pytest.mark.parametrize("operation", list_operations(), ids=lambda item: item.name)
def test_native_function_inventory_and_lossless_execution(operation):
    toolkit = FXMacroDataTools(api_key="")
    tool = toolkit.functions["fxmd_" + operation.name]
    assert isinstance(tool, Function)
    tool.process_entrypoint()
    assert tool.parameters == operation.input_schema
    arguments = {"fixture_argument": {"nested": [1, None]}}
    with patch.object(toolkit._client, "execute", return_value=Result(operation.name, deepcopy(PAYLOAD))) as execute:
        result = tool.entrypoint(**arguments)
    execute.assert_called_once_with(operation.name, arguments)
    assert result["data"] == PAYLOAD
    assert result["records"][0]["announcement_datetime"] == "2026-01-01T12:00:00Z"
    assert result["records"][0]["value"] is None
    assert "utm_source=agno" in result["provider_url"]


def test_agent_and_function_call_are_real_host_objects():
    toolkit = FXMacroDataTools(api_key="", include_tools=["fxmd_data_catalogue"])
    agent = Agent(tools=[toolkit])
    assert agent.tools[0] is toolkit
    tool = toolkit.functions["fxmd_data_catalogue"]
    call = FunctionCall(function=tool, arguments={"currency": "USD"})
    with patch.object(toolkit._client, "execute", return_value=Result("data_catalogue", [])):
        result = call.execute()
    assert result is not None
    assert call.result["records"] == []


def test_exception_details_do_not_escape():
    toolkit = FXMacroDataTools(api_key="DO_NOT_DISCLOSE_SENTINEL")
    with patch.object(
        toolkit._client, "execute", side_effect=RuntimeError("https://example.org/?api_key=DO_NOT_DISCLOSE_SENTINEL")
    ):
        result = toolkit.functions["fxmd_data_catalogue"].entrypoint(currency="USD")
    assert "error" in result
    assert "SENTINEL" not in str(result)
    assert "example.org" not in str(result)


def test_inventory_exact_and_filters_work():
    names = {"fxmd_" + operation.name for operation in list_operations()}
    assert set(FXMacroDataTools(api_key="").functions) == names
    assert set(FXMacroDataTools(api_key="", exclude_tools=["fxmd_health"]).functions) == names - {"fxmd_health"}


@pytest.mark.parametrize("enable_rest,enable_mcp,expected", [(True, False, 23), (False, True, 49), (False, False, 0)])
def test_operation_family_flags(enable_rest, enable_mcp, expected):
    toolkit = FXMacroDataTools(api_key="", enable_rest=enable_rest, enable_mcp=enable_mcp)
    try:
        assert len(toolkit.functions) == expected
        assert all(name.startswith("fxmd_mcp_") == enable_mcp for name in toolkit.functions)
    finally:
        toolkit.close()


def test_close_releases_client_session():
    toolkit = FXMacroDataTools(api_key="")
    with patch.object(toolkit._client, "close") as close:
        toolkit.close()
    close.assert_called_once_with()


def test_optional_dependency_error_is_actionable(monkeypatch):
    import agno.tools.fxmacrodata as module

    spec = importlib.util.spec_from_file_location("fxmacrodata_missing_dependency", module.__file__)
    loaded = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "fxmacrodata_public", None)
    with pytest.raises(ImportError, match="pip install https://github.com/fxmacrodata/") as error:
        spec.loader.exec_module(loaded)
    assert error.value.__suppress_context__ is True


def test_python_39_has_explicit_optional_tool_requirement(monkeypatch):
    import agno.tools.fxmacrodata as module

    spec = importlib.util.spec_from_file_location("fxmacrodata_old_python", module.__file__)
    loaded = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "version_info", (3, 9, 0))
    with pytest.raises(ImportError, match="Python 3.10 or newer"):
        spec.loader.exec_module(loaded)


def test_native_output_redacts_successful_response_credentials():
    from urllib.parse import quote, quote_plus

    import requests

    secret = "synthetic-key +/="
    toolkit = FXMacroDataTools(api_key=secret, include_tools=["fxmd_health"])
    response = requests.Response()
    response.status_code = 200
    response.headers["Content-Type"] = "application/json"
    response._content = json.dumps(
        {
            "data": [{"fixture": "synthetic", "value": None}],
            "apiKey": secret,
            "echo": [secret, quote(secret, safe=""), quote_plus(secret), "Bearer " + secret],
        }
    ).encode()
    response._content_consumed = True
    try:
        call = FunctionCall(function=toolkit.functions["fxmd_health"], arguments={})
        with patch.object(toolkit._client._session, "request", return_value=response):
            call.execute()
        result = call.result
        assert isinstance(result, dict)
        assert result["records"] == [{"fixture": "synthetic", "value": None}]
        for variant in (secret, quote(secret, safe=""), quote_plus(secret)):
            assert variant not in json.dumps(result)
        assert "fxmacrodata.com" in result["provider_url"]
    finally:
        toolkit.close()


def test_public_usd_cookbook_uses_native_functions_and_closes(capsys):
    cookbook = Path(__file__).resolve().parents[5] / "cookbook/91_tools/fxmacrodata/public_usd.py"
    spec = importlib.util.spec_from_file_location("fxmacrodata_public_usd_cookbook", cookbook)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    toolkit = FXMacroDataTools(api_key="", enable_mcp=False)
    try:
        with (
            patch.object(module, "FXMacroDataTools", return_value=toolkit),
            patch.object(toolkit._client, "execute", side_effect=lambda name, args: Result(name, [])) as execute,
            patch.object(toolkit, "close", wraps=toolkit.close) as close,
        ):
            module.main()
        assert [call.args[0] for call in execute.call_args_list] == [
            "data_catalogue",
            "indicator_history",
            "release_calendar",
        ]
        close.assert_called_once_with()
        assert "No records available in this window." in capsys.readouterr().out
    finally:
        toolkit.close()
