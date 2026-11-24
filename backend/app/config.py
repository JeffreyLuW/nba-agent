"""All settings come from environment variables (see .env.example)."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # "provider:model", read by LangChain's init_chat_model.
    # Default is a free local model run by Ollama (no API key, no cost).
    # Paid options: "anthropic:claude-sonnet-5-5", "openai:gpt-5-mini"
    llm_model: str = "ollama:qwen3"
    # Where the Ollama app listens. Only used when llm_model starts with "ollama:".
    ollama_base_url: str = "http://localhost:11434"
    # qwen3 is a "thinking" model. Thinking makes it much slower, and its hidden
    # thinking text never reaches the chat, so the page shows "..." for a long
    # time. Off by default. Set OLLAMA_REASONING=true to try it.
    ollama_reasoning: bool = False
    # How many tokens the model can read at once. Ollama's default (2048-4096)
    # is too small for our system prompt + 6 tool descriptions + tool results.
    ollama_num_ctx: int = 16384
    mcp_server_url: str = "http://localhost:8001/mcp"
    database_url: str = "postgresql+psycopg://nba:nba@localhost:5432/nba"
    frontend_origin: str = "http://localhost:3000"


settings = Settings()
