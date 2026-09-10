"""Run an Agno research agent with public USD tools and an OpenAI model."""

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.tools.fxmacrodata import FXMacroDataTools


def main() -> None:
    toolkit = FXMacroDataTools(
        api_key="",
        include_tools=["fxmd_data_catalogue", "fxmd_indicator_history", "fxmd_release_calendar"],
    )
    try:
        agent = Agent(
            model=OpenAIChat(id="gpt-4.1-mini"),
            tools=[toolkit],
            instructions=(
                "Discover the USD indicator catalogue before choosing an indicator. "
                "State the requested dates, preserve source links, and identify empty results. "
                "Keep published observations separate from predictions and market consensus."
            ),
            add_datetime_to_context=True,
            markdown=True,
        )
        agent.print_response(
            "Use FXMacroData to summarize the latest available US inflation history and the next "
            "30 days of published USD release-calendar events. Include sources and any unavailable data.",
            stream=True,
        )
    finally:
        toolkit.close()


if __name__ == "__main__":
    main()
