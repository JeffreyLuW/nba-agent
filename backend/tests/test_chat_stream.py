"""Test that POST /chat streams the answer AND the tool events.

No real LLM or MCP server: we build a tiny graph with a fake chat model and a
fake tool, and plug it into the app.
"""

import json

from app import main
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition


@tool
def get_player_stats(player_name: str) -> dict:
    """Fake stats tool."""
    return {"player": player_name, "points": 30.1}


def fake_agent():
    llm = FakeMessagesListChatModel(responses=[
        AIMessage("", tool_calls=[{"name": "get_player_stats",
                                   "args": {"player_name": "Stephen Curry"}, "id": "c1"}]),
        AIMessage("Curry averaged 30.1 points."),
    ])

    def call_llm(state: MessagesState):
        return {"messages": [llm.invoke(state["messages"])]}

    graph = StateGraph(MessagesState)
    graph.add_node("agent", call_llm)
    graph.add_node("tools", ToolNode([get_player_stats]))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=InMemorySaver())


def test_chat_streams_text_and_tool_events(monkeypatch):
    monkeypatch.setattr(main, "save_message", lambda *a: None)
    main.app.state.agent = fake_agent()
    client = TestClient(main.app)  # no "with": skips startup, so no MCP server needed

    res = client.post("/chat", json={"message": "Curry 2015-16?", "conversation_id": "t1"})
    lines = [line[6:] for line in res.text.splitlines() if line.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    events = [json.loads(line) for line in lines[:-1]]

    calls = [e for e in events if e.get("type") == "tool_call"]
    results = [e for e in events if e.get("type") == "tool_result"]
    text = "".join(e["text"] for e in events if "text" in e)

    assert calls == [{"type": "tool_call", "name": "get_player_stats",
                      "args": {"player_name": "Stephen Curry"}}]
    assert "30.1" in results[0]["content"]
    assert text == "Curry averaged 30.1 points."
