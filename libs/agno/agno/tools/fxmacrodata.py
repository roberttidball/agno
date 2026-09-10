"""Schema-aware Agno tools backed by FXMacroData's public contracts."""

from __future__ import annotations

import sys
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from agno.tools.function import Function
from agno.tools.toolkit import Toolkit

if sys.version_info < (3, 10):
    raise ImportError("FXMacroDataTools requires Python 3.10 or newer.")

try:
    from fxmacrodata_public import (
        FXMacroDataClient,
        FXMacroDataError,
        Operation,
        list_operations,
    )
except ImportError:
    raise ImportError(
        "FXMacroDataTools needs `fxmacrodata-public-client`. Install it using `pip install "
        "https://github.com/fxmacrodata/fxmacrodata-public-client/releases/download/v0.1.0/"
        "fxmacrodata_public_client-0.1.0-py3-none-any.whl`."
    ) from None

SITE_URL = (
    "https://fxmacrodata.com/?utm_source=agno&utm_medium=integration"
    "&utm_campaign=open_source_integrations&utm_content=app"
)


def _entrypoint(client: FXMacroDataClient, operation: Operation) -> Callable[..., dict[str, Any]]:
    def invoke(**arguments: Any) -> dict[str, Any]:
        try:
            output = client.execute(operation.name, arguments).as_dict()
            output["provider_url"] = SITE_URL
            return output
        except Exception as error:  # noqa: BLE001 - sanitize the external SDK boundary
            # SDK exceptions may contain an authenticated request URL.
            detail = (
                str(error)
                if isinstance(error, FXMacroDataError)
                else "FXMacroData request failed. Check parameters and access."
            )
            return {"error": detail, "operation": operation.name}

    invoke.__name__ = "fxmd_" + operation.name
    invoke.__doc__ = operation.description
    return invoke


class FXMacroDataTools(Toolkit):
    """Expose every public REST operation and hosted MCP tool to Agno.

    Args:
        api_key: Optional user-owned key. None uses FXMACRODATA_API_KEY;
            an empty string explicitly selects no-key access.
        timeout: Maximum request duration in seconds.
        include_tools: Optional native Agno tool-name allowlist. All tools
            are registered when this is omitted.
        exclude_tools: Optional native Agno tool-name denylist.
        enable_rest: Register REST operations (enabled by default).
        enable_mcp: Register hosted MCP tools (enabled by default).
    """

    def __init__(
        self,
        api_key: str | None = None,
        timeout: int = 30,
        include_tools: list[str] | None = None,
        exclude_tools: list[str] | None = None,
        enable_rest: bool = True,
        enable_mcp: bool = True,
    ) -> None:
        self._client = FXMacroDataClient(api_key=api_key, timeout=timeout)
        functions = [
            Function(
                name="fxmd_" + operation.name,
                description=operation.description,
                parameters=deepcopy(operation.input_schema),
                entrypoint=_entrypoint(self._client, operation),
                skip_entrypoint_processing=True,
                strict=False,
            )
            for operation in list_operations()
            if (enable_mcp if operation.method == "MCP" else enable_rest)
        ]
        super().__init__(
            name="fxmacrodata",
            tools=functions,
            include_tools=include_tools,
            exclude_tools=exclude_tools,
            timeout=timeout,
            instructions=(
                "Use fxmd_data_catalogue to discover supported indicator slugs before querying history. "
                "USD catalogue, indicator history and release calendar work without a key. "
                "Keep source URLs and observed announcement timestamps in research answers. "
                "FXMacroData-generated predictions are distinct from market consensus. "
                "Empty records mean unavailable for the selected window; do not invent observations. "
                "MCP visual results preserve resources but this toolkit does not render MCP Apps."
            ),
            add_instructions=True,
        )

    def close(self) -> None:
        """Release the toolkit's HTTP session when it is no longer needed."""
        self._client.close()
