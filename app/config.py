from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    app_base_url: str = os.getenv("APP_BASE_URL", "http://localhost:8000")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./classbrief.sqlite3")
    app_password_hash: str = os.getenv("APP_PASSWORD_HASH", "")
    session_secret: str = os.getenv("SESSION_SECRET", "dev-only-change-me")
    cron_secret: str = os.getenv("CRON_SECRET", "dev-cron-secret")
    iserv_webdav_url: str = os.getenv("ISERV_WEBDAV_URL", "")
    iserv_username: str = os.getenv("ISERV_USERNAME", "")
    iserv_password: str = os.getenv("ISERV_PASSWORD", "")
    plugin_max_context_chars: int = int(os.getenv("PLUGIN_MAX_CONTEXT_CHARS", "80000"))
    ai_provider: str = os.getenv("AI_PROVIDER", "plugin")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5-mini")
    openai_reasoning_model: str = os.getenv("OPENAI_REASONING_MODEL", "")
    chatgpt_plan_model: str = os.getenv("CHATGPT_PLAN_MODEL", "")
    credential_encryption_key: str = os.getenv("CREDENTIAL_ENCRYPTION_KEY", "")
    max_ai_input_chars: int = int(os.getenv("MAX_AI_INPUT_CHARS", "120000"))
    max_document_chars: int = int(os.getenv("MAX_DOCUMENT_CHARS", "50000"))
    max_document_bytes: int = int(os.getenv("MAX_DOCUMENT_BYTES", str(512 * 1024 * 1024)))
    max_batch_chars: int = int(os.getenv("MAX_BATCH_CHARS", "80000"))
    keep_extracted_text: bool = _bool("KEEP_EXTRACTED_TEXT")
    monthly_ai_budget_usd: float = float(os.getenv("MONTHLY_AI_BUDGET_USD", "1.00"))
    processing_lock_timeout_minutes: int = int(os.getenv("PROCESSING_LOCK_TIMEOUT_MINUTES", "10"))
    vapid_public_key: str = os.getenv("VAPID_PUBLIC_KEY", "")
    vapid_private_key: str = os.getenv("VAPID_PRIVATE_KEY", "")
    pebbule_api_url: str = os.getenv("PEBBULE_API_URL", "")
    pebbule_integration_secret: str = os.getenv("PEBBULE_INTEGRATION_SECRET", "")
    pebbule_subject_map: str = os.getenv("PEBBULE_SUBJECT_MAP", "{}")

    @property
    def production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
