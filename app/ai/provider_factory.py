from __future__ import annotations

from sqlalchemy.orm import Session

from app.ai.base import AIProvider
from app.ai.chatgpt_plan_provider import ChatGPTPlanProvider
from app.ai.openai_provider import AIProviderError, OpenAIProvider
from app.config import Settings
from app.services.chatgpt_plan import active_connection


def build_ai_provider(db: Session, settings: Settings) -> tuple[AIProvider, str]:
    connection = active_connection(db)
    if connection is not None:
        return ChatGPTPlanProvider(db, settings, connection), "chatgpt_plan"
    if settings.ai_provider == "openai":
        return OpenAIProvider(settings), "openai"
    raise AIProviderError("Verbinde zuerst dein ChatGPT-Abo in den Einstellungen")
