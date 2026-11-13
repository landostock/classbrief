from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from app.config import get_settings
from app.models import ChatGPTConnection, Lesson, Subject
from app.services.chatgpt_plan import create_pairing, decrypt_credentials, provider_mode
from app.services.schedule import BERLIN


def test_pairing_is_one_time_and_credentials_are_encrypted(client, db, monkeypatch):
    _pairing, token, _ = create_pairing(db)

    async def validated(_id_token: str, client_id: str, nonce: str):
        assert client_id == "oaiapp_test"
        assert nonce == "nonce-test"
        return {"sub": "openai-user-1", "email": "lando@example.com", "nonce": nonce}

    monkeypatch.setattr("app.services.chatgpt_plan.validate_id_token", validated)
    assert client.get("/api/chatgpt/pairing").status_code == 401
    info = client.get("/api/chatgpt/pairing", headers={"Authorization": f"Bearer {token}"})
    assert info.status_code == 200
    assert info.json()["host_id"].startswith("urn:uuid:")

    response = client.post(
        "/api/chatgpt/pairing",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "client_id": "oaiapp_test",
            "id_token": "signed-id-token",
            "access_token": "secret-access-token",
            "refresh_token": "secret-refresh-token",
            "expires_in": 3600,
            "scope": "openid offline_access resource.invoke chatgpt.tokens.use.direct",
            "nonce": "nonce-test",
        },
    )
    assert response.status_code == 200
    connection = db.scalar(select(ChatGPTConnection))
    assert connection is not None
    assert "secret-access-token" not in connection.encrypted_credentials
    assert decrypt_credentials(get_settings(), connection)["access_token"] == "secret-access-token"
    assert provider_mode(db, get_settings()) == "chatgpt_plan"
    assert client.get("/api/chatgpt/pairing", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_central_schedule_and_today_view(auth_client, db):
    subject = Subject(id="mathe", name="Mathe", short_name="Ma", webdav_path="/mathe/")
    now = datetime.now(BERLIN)
    today_start = now.replace(hour=12, minute=0, second=0, microsecond=0)
    lesson = Lesson(
        subject=subject,
        subject_id="mathe",
        scheduled_start=today_start,
        scheduled_end=today_start + timedelta(minutes=45),
        source="pebbule",
        status="scheduled",
        room="0.5",
    )
    records = [subject, lesson]
    if now.weekday() > 4:
        week_start = (now - timedelta(days=now.weekday())).replace(hour=12, minute=0, second=0, microsecond=0)
        records.append(
            Lesson(
                subject=subject,
                subject_id="mathe",
                scheduled_start=week_start,
                scheduled_end=week_start + timedelta(minutes=45),
                source="pebbule",
                status="scheduled",
                room="0.5",
            )
        )
    db.add_all(records)
    db.commit()

    today = auth_client.get("/")
    assert today.status_code == 200
    assert "Dein Schultag" in today.text
    assert "Deine Fächer" not in today.text
    assert "Mathe" in today.text

    schedule = auth_client.get("/schedule")
    assert schedule.status_code == 200
    assert "ZENTRALER STUNDENPLAN" in schedule.text
    assert "Mathe" in schedule.text
