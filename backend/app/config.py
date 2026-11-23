"""All settings come from environment variables (see .env.example)."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # "provider:model", read by LangChain's init_chat_model.
    # Examples: "anthropic:claude-sonnet-5-5", "openai:gpt-5-mini"
    llm_model: str = "anthropic:claude-sonnet-5-5"
    mcp_server_url: str = "http://localhost:8001/mcp"
    database_url: str = "postgresql+psycopg://nba:nba@localhost:5432/nba"
    frontend_origin: str = "http://localhost:3000"


settings = Settings()
