# FXMacroData tools

Use [FXMacroData](https://fxmacrodata.com/?utm_source=agno&utm_medium=integration&utm_campaign=open_source_integrations&utm_content=readme) macroeconomic observations and release calendars in Agno. USD catalogue discovery, indicator history, and release-calendar queries work without an FXMacroData API key.

## Install

Use Python 3.10 or newer with an Agno checkout containing `agno.tools.fxmacrodata`. Install the optional public client from its versioned GitHub release:

```bash
uv pip install https://github.com/fxmacrodata/fxmacrodata-public-client/releases/download/v0.1.0/fxmacrodata_public_client-0.1.0-py3-none-any.whl
```

## Run without credentials

```bash
python cookbook/91_tools/fxmacrodata/public_usd.py
```

This runs real Agno `FunctionCall` objects for the USD catalogue, inflation history, and the next 30 days of release-calendar events. It prints record counts and source links. An empty window remains empty; the integration does not invent observations or future releases.

## Use with an agent

Install `openai` and provide your own `OPENAI_API_KEY` through your process environment, then run:

```bash
uv pip install openai
python cookbook/91_tools/fxmacrodata/research_agent.py
```

The model call uses your model account. The FXMacroData queries in this recipe remain anonymous. `api_key=""` explicitly ignores any ambient FXMacroData key.

## Select tools and access

`FXMacroDataTools()` registers 72 schema-aware Agno functions: 23 REST operations and 49 hosted MCP tools. These cover catalogue discovery, history, release calendars, FX, commodities, COT positioning, availability, revisions, forecasts, analytics, streams, and hosted visual resources. Names are prefixed with `fxmd_`; MCP names begin with `fxmd_mcp_`.

Use `enable_rest=False` or `enable_mcp=False` to disable a family. Agno's `include_tools` and `exclude_tools` filter individual function names. Both families are enabled by default. The complete input schemas remain attached to each native `Function`.

For protected operations, supply your own `FXMACRODATA_API_KEY` in the process environment before constructing `FXMacroDataTools()`, or pass `api_key` at construction. Credentials are not model tool arguments. Each response retains the public payload, records, provenance, and a provider link. The public client validates arguments, bounds requests, redacts credentials from responses and errors, and rejects redirects. Stream calls return bounded event collections. MCP visual resources are preserved as data; this toolkit does not render MCP Apps.

Call `toolkit.close()` in a `finally` block when finished. See the [API reference](https://fxmacrodata.com/documentation/reference?utm_source=agno&utm_medium=integration&utm_campaign=open_source_integrations&utm_content=docs) for parameters, access requirements, and public data semantics. FXMacroData-generated predictions are distinct from market consensus.
