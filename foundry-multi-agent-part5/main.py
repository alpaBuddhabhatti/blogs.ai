import asyncio
import os

from agent_framework import AgentResponseUpdate
from agent_framework.foundry import FoundryChatClient
from agent_framework.orchestrations import SequentialBuilder
from azure.identity import AzureCliCredential
from dotenv import load_dotenv


SCENARIO = """
Investigate this retail scenario:

- September electronics sales declined 41% compared with August across 10 stores.
- Four stores reported low stock for selected electronics products.
- The other six stores did not report those specific stock shortages.
- Management believes inventory is the cause.

Evaluate that hypothesis using only the supplied evidence. Do not invent store-level
sales, inventory quantities, product coverage, or causal explanations. Explain what
additional data is needed before reaching a conclusion.
"""


def required_setting(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"{name} is missing. Set it in your .env file; see .env.example."
        )
    return value


async def main() -> None:
    # Read project settings from .env; AzureCliCredential uses the signed-in az CLI.
    load_dotenv()

    project_endpoint = required_setting("FOUNDRY_PROJECT_ENDPOINT")
    model = required_setting("FOUNDRY_MODEL")

    chat_client = FoundryChatClient(
        project_endpoint=project_endpoint,
        model=model,
        credential=AzureCliCredential(),
    )

    # Share one model client while giving each agent a distinct analysis role.
    sales_agent = chat_client.as_agent(
        name="Sales Analyst",
        instructions=(
            "You are a retail sales analyst. Analyze only the sales facts supplied "
            "in the conversation. State the 41% September-versus-August decline as "
            "a reported fact, not as proof of its cause. Do not invent revenue, "
            "transaction, store, or product figures. Identify sales data needed "
            "to test timing, product, store, channel, and promotion effects. "
            "Clearly label facts, unknowns, and hypotheses."
        ),
    )

    inventory_agent = chat_client.as_agent(
        name="Inventory Analyst",
        instructions=(
            "You are a retail inventory analyst. Assess whether the reported "
            "shortages in four stores could explain the overall 41% decline across "
            "10 stores. Do not assume stores contribute equally to sales and do "
            "not treat 4 of 10 stores as 40% of lost revenue. Consider the limited "
            "scope ('selected products') and distinguish reported low stock from "
            "verified lost sales. State what store- and product-level inventory "
            "and sales data would be needed."
        ),
    )

    advisor_agent = chat_client.as_agent(
        name="Business Advisor",
        instructions=(
            "You are a retail business advisor. Combine the sales and inventory "
            "findings into an executive summary, evidence, hypotheses, and "
            "recommended actions. Separate confirmed facts from assumptions. "
            "Do not claim an unverified cause is proven or invent figures. "
            "Recommend practical next investigations before operational changes."
        ),
    )

    # Run specialists in order and expose their analyses before the advisor's answer.
    workflow = SequentialBuilder(
        participants=[sales_agent, inventory_agent, advisor_agent],
        intermediate_output_from=[sales_agent, inventory_agent],
    ).build()

    print("Running the retail sales investigation...\n")
    last_author: str | None = None
    final_output_seen = False

    # Stream each intermediate analysis and the final recommendation as it arrives.
    async for event in workflow.run(SCENARIO, stream=True):
        if event.type not in ("intermediate", "output"):
            continue
        if not isinstance(event.data, AgentResponseUpdate):
            raise TypeError(
                f"Unexpected workflow update type: {type(event.data).__name__}"
            )

        update = event.data
        author = update.author_name or "Agent"
        if author != last_author:
            if last_author is not None:
                print()
            label = "FINAL" if event.type == "output" else "INTERMEDIATE"
            print(f"[{label}: {author}] ", end="", flush=True)
            last_author = author

        print(update.text, end="", flush=True)
        if event.type == "output":
            final_output_seen = True

    print()
    if not final_output_seen:
        raise RuntimeError("The workflow completed without a final advisor response.")


if __name__ == "__main__":
    asyncio.run(main())
