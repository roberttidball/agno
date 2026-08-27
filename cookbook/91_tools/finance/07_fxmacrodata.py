"""
FinanceTools with FXMacroData
=============================
The same toolkit pointed at currencies instead of equities.

FXMacroData publishes daily FX reference rates from central banks and the BIS,
plus the central-bank press releases behind them. Symbols are currency pairs
(`EUR/USD`, `EURUSD`, `EUR-USD`) across eighteen currencies: AUD, BRL, CAD,
CHF, CNH, CNY, DKK, EUR, GBP, ILS, JPY, NGN, NOK, NZD, PEN, SEK, THB, USD.

Because the dataset has no company-level records, the provider declares only
the four capabilities it can serve - `search_symbols`, `get_quote`,
`get_price_history` and `get_news` - and `FinanceTools` registers only those
tools. The agent never sees `get_financials` or `get_sec_filings` here.

These are end-of-day reference fixings, not venue prices, so they suit
reporting, bookkeeping and macro research rather than execution.

Setup:
    export FXMACRODATA_API_KEY=...   # https://fxmacrodata.com/subscribe
"""

from agno.agent import Agent
from agno.tools.finance import FinanceTools
from agno.tools.finance.providers import FXMacroData

# ---------------------------------------------------------------------------
# Create the Agent
# ---------------------------------------------------------------------------
finance_tools = FinanceTools(provider=FXMacroData())

agent = Agent(
    name="FX Agent",
    model="openai:gpt-5.6",
    tools=[finance_tools],
    instructions=(
        "Lead with the answer, then show the evidence. These are official daily reference"
        " rates, so quote the `as_of` date rather than implying a live price."
    ),
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run the Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("Provider status:", finance_tools.status())
    print("Registered tools:", finance_tools.registered_tools)
    agent.print_response(
        "How has EUR/USD moved over the last month, and what has the ECB said recently?",
        stream=True,
    )
