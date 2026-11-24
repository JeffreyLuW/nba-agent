"""The agent, built as a small LangGraph graph.

    START -> agent --(LLM wants a tool?)--> tools -> agent -> ... -> END

1. "agent" node: the LLM reads the conversation and either answers,
   or asks to call one or more tools.
2. "tools" node: runs the tool calls. The tools come from our MCP server,
   loaded by langchain-mcp-adapters.
3. Loop until the LLM answers without asking for a tool.

Later this is where we add more nodes or more agents (multi-agent).
"""

from langchain.chat_models import init_chat_model
from langchain_core.messages import SystemMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.config import settings

SYSTEM_PROMPT = (
    "You are a fun, expert NBA assistant. Use the tools to get real data. "
    "Never invent stats. If a tool fails, say so."
)


def make_llm():
    """Create the chat model named in LLM_MODEL (e.g. "ollama:qwen3")."""
    if settings.llm_model.startswith("ollama:"):
        # Ollama runs on your computer, so we must tell LangChain where it is.
        return init_chat_model(settings.llm_model, base_url=settings.ollama_base_url)
    # Anthropic / OpenAI read their API key from the environment.
    return init_chat_model(settings.llm_model)


async def build_agent():
    # 1. Connect to the MCP server and turn its tools into LangChain tools.
    client = MultiServerMCPClient(
        {"nba": {"url": settings.mcp_server_url, "transport": "streamable_http"}}
    )
    tools = await client.get_tools()

    # 2. The LLM, told which tools it may call.
    llm = make_llm().bind_tools(tools)

    async def call_llm(state: MessagesState):
        messages = [SystemMessage(SYSTEM_PROMPT), *state["messages"]]
        return {"messages": [await llm.ainvoke(messages)]}

    # 3. The graph.
    graph = StateGraph(MessagesState)
    graph.add_node("agent", call_llm)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)  # -> "tools" or END
    graph.add_edge("tools", "agent")

    # InMemorySaver keeps chat history per thread_id while the server runs.
    # TODO: switch to a Postgres checkpointer (langgraph-checkpoint-postgres).
    return graph.compile(checkpointer=InMemorySaver())
