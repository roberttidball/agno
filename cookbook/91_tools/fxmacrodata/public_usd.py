"""Query public USD data through Agno without an LLM or an API key."""

from datetime import datetime, timedelta, timezone

from agno.tools.function import FunctionCall
from agno.tools.fxmacrodata import FXMacroDataTools


def main() -> None:
    toolkit = FXMacroDataTools(api_key="", enable_mcp=False)
    today = datetime.now(timezone.utc).date()
    calls = [
        ("data_catalogue", {"currency": "USD"}),
        (
            "indicator_history",
            {
                "currency": "USD",
                "indicator": "inflation",
                "start_date": (today - timedelta(days=365)).isoformat(),
                "end_date": today.isoformat(),
            },
        ),
        (
            "release_calendar",
            {
                "currency": "USD",
                "start_date": today.isoformat(),
                "end_date": (today + timedelta(days=30)).isoformat(),
            },
        ),
    ]
    try:
        for operation, arguments in calls:
            call = FunctionCall(function=toolkit.functions["fxmd_" + operation], arguments=arguments)
            call.execute()
            result = call.result
            if not isinstance(result, dict) or result.get("error"):
                raise RuntimeError("The FXMacroData query failed. Check parameters and access.")
            print(f"{operation}: {len(result['records'])} records")
            print(result["source_url"])
            print(result["provider_url"])
            if not result["records"]:
                print("No records available in this window.")
    finally:
        toolkit.close()


if __name__ == "__main__":
    main()
