import json
from urllib.parse import parse_qs, urlparse
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import get_settings
from app.models import Briefing, BriefingMaterial, FileRecord, Lesson, Subject
from app.main import legacy_brief_blocks
from app.routes.web import _briefing_has_new_material, _briefing_is_empty
from app.services.security import hash_password, sign_session, verify_password, verify_session
from app.services.web_access import create_web_access_link


def test_password_hash_and_session():
    stored = hash_password("geheim")
    assert verify_password("geheim", stored)
    assert not verify_password("falsch", stored)
    token = sign_session("secret")
    assert verify_session(token, "secret")
    assert not verify_session(token + "x", "secret")


def test_login_and_health(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/").status_code == 200  # follows redirect to login
    assert client.post("/login", data={"password": "wrong"}).status_code == 401
    assert client.post("/login", data={"password": "classbrief"}, follow_redirects=False).status_code == 303


def test_one_time_access_link_sets_personal_password(client, db):
    link = create_web_access_link(db, get_settings(), lifetime_minutes=15)
    token = parse_qs(urlparse(str(link["url"])).query)["token"][0]

    page = client.get(f"/access?token={token}")
    assert page.status_code == 200
    assert "Dein Passwort festlegen" in page.text
    mismatch = client.post(
        "/access",
        data={"token": token, "password": "eine-sehr-lange-passphrase", "password_confirm": "anders-aber-lang-genug"},
    )
    assert mismatch.status_code == 400

    saved = client.post(
        "/access",
        data={
            "token": token,
            "password": "eine-sehr-lange-passphrase",
            "password_confirm": "eine-sehr-lange-passphrase",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert saved.headers["location"] == "/"
    assert client.get(f"/access?token={token}").status_code == 400

    client.post("/logout", follow_redirects=False)
    assert client.post("/login", data={"password": "classbrief"}).status_code == 401
    assert client.post(
        "/login", data={"password": "eine-sehr-lange-passphrase"}, follow_redirects=False
    ).status_code == 303


def test_cron_authentication(client):
    assert client.post("/internal/tick").status_code == 401
    response = client.post("/internal/tick", headers={"Authorization": "Bearer test-cron-secret"})
    assert response.status_code == 200
    assert response.json()["due_lessons"] == 0


def test_dashboard_requires_login_and_then_loads(client, auth_client):
    response = auth_client.get("/")
    assert response.status_code == 200
    assert "ClassBrief" in response.text


def test_integrations_page_loads(auth_client):
    response = auth_client.get("/settings/integrations")
    assert response.status_code == 200
    assert "Verbindungen" in response.text
    assert "IServ WebDAV" in response.text
    assert "ClassBrief-Plugin" in response.text


def test_plugin_mode_usage_page_has_no_api_budget_counter(auth_client):
    response = auth_client.get("/settings/usage")
    assert response.status_code == 200
    assert "Plugin-Modus aktiv" in response.text
    assert "von $" not in response.text


def test_sport_cannot_be_prepared_manually(auth_client, db):
    db.add(Subject(
        id="sport", name="Sport", short_name="Spo", webdav_path="/sport/",
        briefing_enabled=False,
    ))
    db.commit()

    response = auth_client.post("/api/subjects/sport/prepare")

    assert response.status_code == 409
    assert response.json()["detail"] == "Für dieses Fach sind keine Briefings vorgesehen."


def test_legacy_briefing_prose_is_split_into_readable_blocks():
    blocks = legacy_brief_blocks("EINLEITUNG: Basisdaten. HAUPTTEIL: 1. Szene einordnen. 2. Sprache deuten. SCHLUSS: Synthese.")
    assert [item["type"] for item in blocks].count("heading") == 3
    assert any(item["type"] == "numbered" and len(item["items"]) == 2 for item in blocks)


def test_no_new_material_is_a_valid_continuity_briefing():
    briefing = type("BriefingStub", (), {
        "probable_topic": "Szeneninterpretation fortsetzen",
        "quick_brief": "Keine neuen relevanten Materialien. Anschluss an den bestätigten Stand.",
        "medium_brief": "Zuletzt wurde die Figurenanalyse begonnen.",
        "deep_brief_json": json.dumps({"new_materials": [], "connections": ["Figurenanalyse fortsetzen"]}),
    })()

    assert _briefing_is_empty(briefing) is False
    assert _briefing_has_new_material(briefing) is False


def test_only_technically_contentless_briefing_is_empty():
    briefing = type("BriefingStub", (), {
        "probable_topic": "",
        "quick_brief": "",
        "medium_brief": "",
        "deep_brief_json": "{}",
    })()

    assert _briefing_is_empty(briefing) is True


def test_deleting_briefing_releases_its_material(auth_client, db):
    now = datetime.now(timezone.utc)
    subject = Subject(id="de", name="Deutsch", short_name="De", webdav_path="/de/")
    db.add(subject)
    db.flush()
    lesson = Lesson(subject_id="de", scheduled_start=now + timedelta(hours=1), scheduled_end=now + timedelta(hours=2))
    file = FileRecord(subject_id="de", relative_path="/de/neu.pdf", filename="neu.pdf", sha256="a" * 64, processing_status="already_briefed")
    db.add_all([lesson, file])
    db.flush()
    briefing = Briefing(
        lesson_id=lesson.id, subject_id="de", material_state_hash="b" * 64,
        probable_topic="Test", quick_brief="Kurz", medium_brief="Mittel",
        deep_brief_json='{"summary":"Details","prior_knowledge":[],"new_materials":[],"important_terms":[],"likely_tasks":[],"pitfalls":[],"sources":[]}',
    )
    db.add(briefing)
    db.flush()
    db.add(BriefingMaterial(briefing_id=briefing.id, file_id=file.id, sha256=file.sha256))
    db.commit()
    briefing_id, file_id = briefing.id, file.id

    response = auth_client.post(f"/briefings/{briefing_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    db.expire_all()
    assert db.get(Briefing, briefing_id) is None
    assert db.scalar(select(FileRecord).where(FileRecord.id == file_id)).processing_status == "context_ready"
