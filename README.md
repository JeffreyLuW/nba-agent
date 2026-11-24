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
│ Next.js      │                          │ FastAPI + LangGraph  │──▶ LLM (Ollama on
│ :3000        │                          │ :8000                │    your Mac, free)
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
| `mcp_server/` | An MCP server. It wraps free NBA data (`nba_api` + RSS news feeds) and exposes 6 **tools** (see below). `cache.py` keeps answers in memory so we don't hit NBA.com rate limits. |
| `backend/`    | FastAPI app. `app/agent.py` builds the LangGraph agent and loads the MCP tools with `langchain-mcp-adapters`. `app/main.py` streams answers at `POST /chat`. |
| `frontend/`   | Next.js (TypeScript) chat page. Placeholder UI. |
| `db/`         | `init.sql` creates the Postgres tables (conversations, messages). |
| `evals/`      | 23 test questions (`dataset.jsonl`) and a script that scores the agent (`run_evals.py`, `checks.py`). |
| `.github/workflows/` | CI: lint, unit tests, import check, frontend build. |

## MCP tools

| Tool | What it answers | Example question |
|------|-----------------|------------------|
| `get_player_stats` | Per-game averages for one season, a playoff run, or a career | "Curry's points in 2015-16?" |
| `get_player_game_log` | Game-by-game box scores, newest first or best first | "Kobe's best game in 2005-06?" |
| `compare_players` | Two players side by side, with the difference and who is better | "Curry or LeBron in 2015-16?" |
| `get_team_standings` | Wins, losses, rank, games back, streak (filter by conference or team) | "Who leads the West?" |
| `get_todays_games` | Today's games with live scores | "What games are on tonight?" |
| `search_news` | Latest headlines from ESPN, CBS Sports and Yahoo RSS feeds | "Any Lakers news?" |

All tools return JSON. If something goes wrong (unknown player, bad season,
NBA.com down) a tool returns `{"error": "..."}` instead of crashing, so the LLM
can tell you the truth instead of guessing.

**Caching.** stats.nba.com blocks clients that call it too often. So each answer
is kept in memory for a while (season stats: 6 hours, standings and game logs:
30 minutes, live scores: 30 seconds, news: 10 minutes), and there is a short
pause between two real calls. See `mcp_server/cache.py`.

### Key ideas you can learn here

- **Tool use**: the LLM does not fetch data itself. It *asks* to call a tool, the
  graph runs the tool, and the result goes back to the LLM.
- **MCP**: a standard protocol for tools. Our tools live in a separate server, so
  any MCP client (this agent, Claude Desktop, MCP Inspector) can use them.
- **LangGraph**: the agent is a graph: `agent → tools → agent → … → answer`.
  New nodes and more agents (multi-agent) are added here later.
- **Tool descriptions are prompts**: the LLM picks a tool by reading its
  docstring in `server.py`. Clear docstrings with examples = better tool choice.
- **Mocking**: unit tests replace NBA.com with fake data, so tests are fast,
  free and work without internet.
- **Evals**: a fixed question set to check the agent still works after changes.
  Unlike unit tests, they test the LLM + tools together.

## Run it locally

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) and
[Ollama](https://ollama.com/download). Ollama runs the LLM on your own computer,
so it costs nothing and needs no API key.

```bash
# 1. Install Ollama (or download the app from ollama.com), then open it once.
brew install ollama
# 2. Download the model (about 5 GB, one time only).
ollama pull qwen3
# 3. Start everything.
cp .env.example .env
docker compose up --build
```

Ollama runs on your Mac (not inside Docker). The backend container reaches it at
`http://host.docker.internal:11434`.

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

### Run the unit tests

No Docker, no LLM and no internet needed: NBA.com is mocked.

```bash
pip install -r requirements-dev.txt -r backend/requirements.txt
pytest
```

### Run evals

Evals ask the real agent real questions. You need everything running
(`docker compose up` + Ollama), then:

```bash
python evals/run_evals.py              # all 23 questions (takes a few minutes)
python evals/run_evals.py --only stats # only ids starting with "stats"
python evals/run_evals.py --verbose    # also print the answers
```

Each question is scored with three checks (see `evals/checks.py`):

| Check | Question it answers |
|-------|---------------------|
| `tools` | Did the agent call the right tool, with the right arguments (e.g. `season="2015-16"`)? |
| `contains` | Does the answer have the known correct value (e.g. Curry 2015-16 = `30.1`)? |
| `grounded` | Is every number in the answer found in a tool result? If not, the LLM invented it. |

The backend stream sends tool calls and tool results as extra events, so the
eval script (and the web page) can see which tools the agent used. Results are
saved to `evals/results/` so you can compare models, for example
`ollama:qwen3` vs. `ollama:qwen3:4b`.

## Branches

- `nprd` – staging. Open PRs into this branch first.
- `main` – production.

CI runs on every PR and on pushes to both branches.

## Change the LLM

Set `LLM_MODEL` in `.env`, as `provider:model`.

- Free, local (default): `ollama:qwen3`. Any Ollama model with **tool calling**
  works, e.g. `ollama:llama3.1` or `ollama:qwen3:4b` (smaller, faster). Run
  `ollama pull <model>` first.
- Paid: `anthropic:claude-sonnet-5-5` or `openai:gpt-5-mini`, plus that
  provider's API key in `.env`.

Restart after a change: `docker compose up --build backend`.

Ollama settings (optional, in `.env`):
- `OLLAMA_REASONING=false` (default). qwen3 can "think" before it answers. That
  is much slower, and the thinking is hidden, so the page shows "…" for a long
  time. Set `true` to try it.
- `OLLAMA_NUM_CTX=16384` (default). How much text the model can read at once.
  Ollama's own default is too small for our tools, and the model then gives
  empty or confused answers.

## Roadmap

- [x] Real news source for `search_news`
- [x] Player game logs, comparisons, standings, caching
- [x] First eval set (tool choice + no invented numbers)
- [ ] Save chat history in Postgres (LangGraph Postgres checkpointer)
- [ ] Better UI (the fun part)
- [ ] Features: debate mode, what-if simulator, player cards
- [ ] More evals (LLM-as-judge), scored in CI
- [ ] Deploy to AWS
