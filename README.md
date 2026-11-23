# 🏀 NBA Agent

An AI agent for NBA fans. You chat with it, and it uses **real NBA data** through
an **MCP server** to answer questions.

This repo is a skeleton. Features (debate mode, what-if simulator, player cards, ...)
come later.

## Architecture

```
 Browser
   │
   ▼
┌──────────────┐  HTTP + streaming (SSE)  ┌──────────────────────┐
│ frontend     │ ───────────────────────▶ │ backend              │
│ Next.js      │                          │ FastAPI + LangGraph  │──▶ LLM API
│ :3000        │                          │ :8000                │    (your key)
└──────────────┘                          └──────────┬───────────┘
                                                     │ MCP (streamable HTTP)
                                 ┌───────────────────┴──────────────┐
                                 ▼                                  ▼
                       ┌──────────────────┐                ┌──────────────┐
                       │ mcp_server       │                │ postgres     │
                       │ FastMCP + nba_api│                │ :5432        │
                       │ :8001            │                └──────────────┘
                       └────────┬─────────┘
                                ▼
                         stats.nba.com (free NBA data)
```

| Folder        | What it is |
|---------------|------------|
| `mcp_server/` | An MCP server. It wraps the free `nba_api` package and exposes **tools**: `get_player_stats`, `get_todays_games`, `search_news` (stub). |
| `backend/`    | FastAPI app. `app/agent.py` builds the LangGraph agent and loads the MCP tools with `langchain-mcp-adapters`. `app/main.py` streams answers at `POST /chat`. |
| `frontend/`   | Next.js (TypeScript) chat page. Placeholder UI. |
| `db/`         | `init.sql` creates the Postgres tables (conversations, messages). |
| `evals/`      | A small question set and a script that scores the agent. |
| `.github/workflows/` | CI: lint, import check, frontend build. |

### Key ideas you can learn here

- **Tool use**: the LLM does not fetch data itself. It *asks* to call a tool, the
  graph runs the tool, and the result goes back to the LLM.
- **MCP**: a standard protocol for tools. Our tools live in a separate server, so
  any MCP client (this agent, Claude Desktop, MCP Inspector) can use them.
- **LangGraph**: the agent is a graph: `agent → tools → agent → … → answer`.
  New nodes and more agents (multi-agent) are added here later.
- **Evals**: a fixed question set to check the agent still works after changes.

## Run it locally

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/).

```bash
cp .env.example .env        # then put your LLM API key in .env
docker compose up --build
```

Then open http://localhost:3000 and ask: *"How many points did Stephen Curry average in 2015-16?"*

Useful URLs:
- http://localhost:8000/docs – backend API docs (Swagger)
- http://localhost:8000/health – backend health check
- http://localhost:8001/mcp – MCP server endpoint

### Test the MCP server alone

```bash
npx @modelcontextprotocol/inspector
# Transport: Streamable HTTP, URL: http://localhost:8001/mcp
```

### Run without Docker (optional)

```bash
# terminal 1
cd mcp_server && pip install -r requirements.txt && python server.py
# terminal 2 (needs Postgres running, or chats just won't be saved)
cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload
# terminal 3
cd frontend && npm install && npm run dev
```

### Run evals

With everything running: `python evals/run_evals.py`

## Branches

- `nprd` – staging. Open PRs into this branch first.
- `main` – production.

CI runs on every PR and on pushes to both branches.

## Change the LLM

Set `LLM_MODEL` in `.env`, as `provider:model`, for example
`anthropic:claude-sonnet-5-5` or `openai:gpt-5-mini`, and set that provider's API key.

## Roadmap

- [ ] Real news source for `search_news`
- [ ] Save chat history in Postgres (LangGraph Postgres checkpointer)
- [ ] Better UI (the fun part)
- [ ] Features: debate mode, what-if simulator, player cards
- [ ] More evals, scored in CI
- [ ] Deploy to AWS
