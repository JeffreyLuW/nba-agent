"""All settings come from environment variables (see .env.example)."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # "provider:model", read by LangChain's init_chat_model.
    # Default is a free local model run by Ollama (no API key, no cost).
    # Paid options: "anthropic:claude-sonnet-5-5", "openai:gpt-5-mini"
    llm_model: str = "ollama:qwen3"
    # Where the Ollama app listens. Only used when llm_model starts with "ollama:".
    ollama_base_url: str = "http://localhost:11434"
    mcp_server_url: str = "http://localhost:8001/mcp"
    database_url: str = "postgresql+psycopg://nba:nba@localhost:5432/nba"
    frontend_origin: str = "http://localhost:3000"


settings = Settings()
