"""Database helpers. Tables are created by db/init.sql."""

from sqlalchemy import create_engine, text

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)


def save_message(conversation_id: str, role: str, content: str) -> None:
    """Store one chat message. Later we can use this for history and evals."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO conversations (id) VALUES (:id) ON CONFLICT DO NOTHING"
            ),
            {"id": conversation_id},
        )
        conn.execute(
            text(
                "INSERT INTO messages (conversation_id, role, content) "
                "VALUES (:cid, :role, :content)"
            ),
            {"cid": conversation_id, "role": role, "content": content},
        )
