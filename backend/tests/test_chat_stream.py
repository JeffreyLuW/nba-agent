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


TOOL_CALL = AIMessage("", tool_calls=[{"name": "get_player_stats",
                                        "args": {"player_name": "Stephen Curry"}, "id": "c1"}])


def fake_agent(responses=None):
    llm = FakeMessagesListChatModel(
        responses=responses or [TOOL_CALL, AIMessage("Curry averaged 30.1 points.")]
    )

    def call_llm(state: MessagesState):
        return {"messages": [llm.invoke(state["messages"])]}

    graph = StateGraph(MessagesState)
    graph.add_node("agent", call_llm)
    graph.add_node("tools", ToolNode([get_player_stats]))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=InMemorySaver())


def chat(monkeypatch, agent) -> list[dict]:
    """POST /chat with a fake agent and return the streamed events."""
    monkeypatch.setattr(main, "save_message", lambda *a: None)
    main.app.state.agent = agent
    client = TestClient(main.app)  # no "with": skips startup, so no MCP server needed

    res = client.post("/chat", json={"message": "Curry 2015-16?", "conversation_id": "t1"})
    lines = [line[6:] for line in res.text.splitlines() if line.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    return [json.loads(line) for line in lines[:-1]]


def test_chat_streams_text_and_tool_events(monkeypatch):
    events = chat(monkeypatch, fake_agent())

    calls = [e for e in events if e.get("type") == "tool_call"]
    results = [e for e in events if e.get("type") == "tool_result"]
    text = "".join(e["text"] for e in events if "text" in e)

    assert calls == [{"type": "tool_call", "name": "get_player_stats",
                      "args": {"player_name": "Stephen Curry"}}]
    assert "30.1" in results[0]["content"]
    assert text == "Curry averaged 30.1 points."


def test_empty_model_answer_gets_a_message(monkeypatch):
    # e.g. a "thinking" model that only produced hidden thinking text
    events = chat(monkeypatch, fake_agent([TOOL_CALL, AIMessage("")]))
    assert "".join(e["text"] for e in events if "text" in e) == main.EMPTY_ANSWER


def test_agent_crash_is_sent_as_error_event(monkeypatch):
    class BrokenAgent:
        async def astream(self, *args, **kwargs):
            raise ConnectionError("Ollama is not running")
            yield  # makes this an async generator

    events = chat(monkeypatch, BrokenAgent())
    assert events == [{"type": "error", "message": "ConnectionError: Ollama is not running"}]


def test_ollama_thinking_off_and_bigger_context():
    from app import agent

    llm = agent.make_llm()
    assert llm.reasoning is False
    assert llm.num_ctx == 16384
