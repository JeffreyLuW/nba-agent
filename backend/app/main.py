"""FastAPI app. Serves the agent over HTTP with streaming.

POST /chat  {"message": "...", "conversation_id": "..."}
returns Server-Sent Events (SSE). Each event is one "data:" line with JSON:

    {"text": "..."}                                   a piece of the answer
    {"type": "tool_call", "name": "...", "args": {}}  the agent calls a tool
    {"type": "tool_result", "name": "...", "content": "..."}  what the tool returned

and at the end "data: [DONE]". The tool events let the UI show what the agent
is doing, and let evals check which tool it used.
"""

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

from app.agent import build_agent
from app.config import settings
from app.db import save_message

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build the agent once, when the server starts.
    app.state.agent = await build_agent()
    yield


app = FastAPI(title="NBA Agent API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_methods=["*"],
    allow_headers=["*"],
)


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


def tool_events(update: dict) -> list[dict]:
    """Find tool calls and tool results in one LangGraph "updates" item."""
    events = []
    for node_update in update.values():
        for msg in (node_update or {}).get("messages", []):
            if isinstance(msg, AIMessage):
                for call in msg.tool_calls:
                    events.append({"type": "tool_call", "name": call["name"], "args": call["args"]})
            elif isinstance(msg, ToolMessage):
                content = msg.content
                if isinstance(content, list):  # MCP results can be a list of blocks
                    content = "".join(
                        b.get("text", "") if isinstance(b, dict) else str(b) for b in content
                    )
                events.append({"type": "tool_result", "name": msg.name, "content": content})
    return events


class ChatRequest(BaseModel):
    message: str
    conversation_id: str = "default"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat")
async def chat(req: ChatRequest):
    agent = app.state.agent
    config = {"configurable": {"thread_id": req.conversation_id}}

    async def event_stream():
        answer = ""
        # Two stream modes at once:
        #  "messages" -> LLM tokens as they are generated (for the live answer)
        #  "updates"  -> each finished graph step (to see tool calls and results)
        async for mode, data in agent.astream(
            {"messages": [HumanMessage(req.message)]},
            config,
            stream_mode=["messages", "updates"],
        ):
            if mode == "messages":
                chunk, meta = data
                # AIMessageChunk = streamed piece; AIMessage = models that do not stream.
                if isinstance(chunk, AIMessage) and meta.get("langgraph_node") == "agent":
                    text = chunk.text  # ignores tool-call parts
                    if text:
                        answer += text
                        yield sse({"text": text})
            else:
                for event in tool_events(data):
                    yield sse(event)
        yield "data: [DONE]\n\n"

        try:
            save_message(req.conversation_id, "user", req.message)
            save_message(req.conversation_id, "assistant", answer)
        except Exception:  # the chat should still work if the DB is down
            log.exception("Could not save messages")

    return StreamingResponse(event_stream(), media_type="text/event-stream")
