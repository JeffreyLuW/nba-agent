"""FastAPI app. Serves the agent over HTTP with streaming.

POST /chat  {"message": "...", "conversation_id": "..."}
returns Server-Sent Events (SSE): one "data:" line per text chunk,
then "data: [DONE]".
"""

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessageChunk, HumanMessage
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
        # stream_mode="messages" gives us LLM tokens as they are generated.
        async for chunk, meta in agent.astream(
            {"messages": [HumanMessage(req.message)]}, config, stream_mode="messages"
        ):
            if isinstance(chunk, AIMessageChunk) and meta.get("langgraph_node") == "agent":
                text = chunk.text  # ignores tool-call parts
                if text:
                    answer += text
                    yield f"data: {json.dumps({'text': text})}\n\n"
        yield "data: [DONE]\n\n"

        try:
            save_message(req.conversation_id, "user", req.message)
            save_message(req.conversation_id, "assistant", answer)
        except Exception:  # the chat should still work if the DB is down
            log.exception("Could not save messages")

    return StreamingResponse(event_stream(), media_type="text/event-stream")
