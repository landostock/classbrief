from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AIUsage


def month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc)


def current_spend(db: Session) -> float:
    return float(db.scalar(select(func.coalesce(func.sum(AIUsage.estimated_cost), 0)).where(AIUsage.timestamp >= month_start())) or 0)


def automatic_budget_available(db: Session, settings: Settings) -> bool:
    return current_spend(db) < settings.monthly_ai_budget_usd


def record_usage(db: Session, *, model: str, input_tokens: int, output_tokens: int, purpose: str, provider: str = "openai") -> AIUsage:
    input_rate = float(os.getenv("OPENAI_INPUT_COST_PER_MILLION", "0"))
    output_rate = float(os.getenv("OPENAI_OUTPUT_COST_PER_MILLION", "0"))
    cost = 0.0 if provider == "chatgpt_plan" else input_tokens / 1_000_000 * input_rate + output_tokens / 1_000_000 * output_rate
    usage = AIUsage(provider=provider, model=model, input_tokens=input_tokens, output_tokens=output_tokens, estimated_cost=cost, purpose=purpose)
    db.add(usage)
    db.flush()
    return usage
