from datetime import datetime, timezone
from dataclasses import replace

from app.config import get_settings
from app.models import AIUsage
from app.services.cost_tracker import automatic_budget_available, current_spend
from app.services.lesson_preparation import _state_hash


def test_material_state_hash_is_order_independent():
    assert _state_hash("ges", 7, ["b", "a"], 2) == _state_hash("ges", 7, ["a", "b"], 2)
    assert _state_hash("ges", 7, ["a"], 2) != _state_hash("ges", 7, ["a"], 3)


def test_budget_limit(db, monkeypatch):
    settings = replace(get_settings(), monthly_ai_budget_usd=1.0)
    db.add(AIUsage(provider="openai", model="test", input_tokens=1, output_tokens=1, estimated_cost=1.25, purpose="test", timestamp=datetime.now(timezone.utc)))
    db.commit()
    assert current_spend(db) == 1.25
    assert automatic_budget_available(db, settings) is False
