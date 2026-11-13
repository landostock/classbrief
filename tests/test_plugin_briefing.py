from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.models import BriefingMaterial, FileRecord, Lesson, Subject
from app.services.plugin_briefing import collect_lesson_context, save_plugin_briefing
from app.services.webdav import WebDAVDownload, WebDAVItem


class FakeWebDAVClient:
    items: list[WebDAVItem] = []
    payloads: dict[str, bytes] = {}

    def __init__(self, *_args, **_kwargs):
        pass

    async def walk_files(self, _path: str):
        return self.items

    async def download_file_to_path(self, path, destination, *, max_bytes=None):
        body = self.payloads[path]
        assert max_bytes is None or len(body) <= max_bytes
        destination.write_bytes(body)
        return WebDAVDownload(hashlib.sha256(body).hexdigest(), len(body))


def _lesson_pair(db):
    now = datetime.now(timezone.utc)
    subject = Subject(id="ges", name="Geschichte", short_name="Ges", webdav_path="/ges/")
    previous = Lesson(
        subject_id="ges",
        scheduled_start=now - timedelta(days=1, minutes=45),
        scheduled_end=now - timedelta(days=1),
    )
    target = Lesson(
        subject_id="ges",
        scheduled_start=now + timedelta(hours=1),
        scheduled_end=now + timedelta(hours=2),
    )
    db.add_all([subject, previous, target])
    db.commit()
    return target, now


def _payload():
    return {
        "probable_topic": "Weimarer Verfassung",
        "quick_brief": "Artikel 48 und die Rolle des Reichspräsidenten.",
        "medium_brief": "Die neue Datei knüpft an die Weimarer Verfassung an.",
        "deep": {
            "summary": "Zusammenhang zwischen Verfassung und Notverordnungen.",
            "prior_knowledge": ["Weimarer Republik"],
            "new_materials": ["Neues Arbeitsblatt"],
            "important_terms": [{"term": "Artikel 48", "explanation": "Notverordnungsrecht"}],
            "likely_tasks": ["Quellentext einordnen"],
            "pitfalls": ["Vermutung nicht als behandelten Stoff ausgeben"],
            "sources": [{"file": "neu.txt", "pages": []}],
        },
    }


@pytest.mark.asyncio
async def test_initial_plugin_scan_excludes_old_folder_inventory(monkeypatch, db):
    target, now = _lesson_pair(db)
    old_path = "/ges/alt.txt"
    new_path = "/ges/neu.txt"
    FakeWebDAVClient.items = [
        WebDAVItem(old_path, "alt.txt", False, '"old"', now - timedelta(days=30), 3, "text/plain"),
        WebDAVItem(new_path, "neu.txt", False, '"new"', now, 120, "text/plain"),
    ]
    FakeWebDAVClient.payloads = {
        old_path: b"alt",
        new_path: b"Neues Material zur Weimarer Verfassung und zu Artikel 48.",
    }
    monkeypatch.setattr("app.services.plugin_briefing.WebDAVClient", FakeWebDAVClient)
    settings = replace(
        get_settings(),
        iserv_webdav_url="https://iserv.example/webdav",
        iserv_username="user",
        iserv_password="secret",
    )

    context = await collect_lesson_context(db, target, settings, now=now)

    assert [item["filename"] for item in context["materials"]] == ["neu.txt"]
    old_record = db.scalar(select(FileRecord).where(FileRecord.relative_path == old_path))
    assert old_record.processing_status == "historical"


@pytest.mark.asyncio
async def test_saved_material_is_not_returned_again(monkeypatch, db):
    target, now = _lesson_pair(db)
    path = "/ges/neu.txt"
    body = b"Neues Material zur Weimarer Verfassung und zu Artikel 48."
    FakeWebDAVClient.items = [WebDAVItem(path, "neu.txt", False, '"new"', now, len(body), "text/plain")]
    FakeWebDAVClient.payloads = {path: body}
    monkeypatch.setattr("app.services.plugin_briefing.WebDAVClient", FakeWebDAVClient)
    settings = replace(
        get_settings(),
        iserv_webdav_url="https://iserv.example/webdav",
        iserv_username="user",
        iserv_password="secret",
    )

    first = await collect_lesson_context(db, target, settings, now=now)
    digest = hashlib.sha256(body).hexdigest()
    save_plugin_briefing(db, target, _payload(), [digest])
    second = await collect_lesson_context(db, target, settings, now=now + timedelta(minutes=1))

    assert first["source_hashes"] == [digest]
    assert second["materials"] == []
    assert db.scalar(select(func.count()).select_from(BriefingMaterial)) == 1


@pytest.mark.asyncio
async def test_unsaved_context_remains_available_after_scan_window_advances(monkeypatch, db):
    target, now = _lesson_pair(db)
    path = "/ges/neu.txt"
    body = b"Neues Material zur Weimarer Verfassung und zu Artikel 48."
    FakeWebDAVClient.items = [WebDAVItem(path, "neu.txt", False, '"new"', now, len(body), "text/plain")]
    FakeWebDAVClient.payloads = {path: body}
    monkeypatch.setattr("app.services.plugin_briefing.WebDAVClient", FakeWebDAVClient)
    settings = replace(
        get_settings(),
        iserv_webdav_url="https://iserv.example/webdav",
        iserv_username="user",
        iserv_password="secret",
    )

    first = await collect_lesson_context(db, target, settings, now=now)
    second = await collect_lesson_context(db, target, settings, now=now + timedelta(minutes=1))

    assert first["source_hashes"]
    assert second["source_hashes"] == first["source_hashes"]
    assert [item["filename"] for item in second["materials"]] == ["neu.txt"]


@pytest.mark.asyncio
async def test_new_path_after_baseline_can_be_relevant_even_with_old_mtime(monkeypatch, db):
    target, now = _lesson_pair(db)
    baseline_path = "/ges/archiv.txt"
    FakeWebDAVClient.items = [
        WebDAVItem(baseline_path, "archiv.txt", False, '"a"', now - timedelta(days=60), 3, "text/plain")
    ]
    FakeWebDAVClient.payloads = {baseline_path: b"alt"}
    monkeypatch.setattr("app.services.plugin_briefing.WebDAVClient", FakeWebDAVClient)
    settings = replace(
        get_settings(),
        iserv_webdav_url="https://iserv.example/webdav",
        iserv_username="user",
        iserv_password="secret",
    )
    await collect_lesson_context(db, target, settings, now=now)

    copied_path = "/ges/neu-kopiert.txt"
    copied_body = b"Heute neu kopiertes Arbeitsblatt mit erhaltenem alten Dateidatum."
    FakeWebDAVClient.items = [
        WebDAVItem(baseline_path, "archiv.txt", False, '"a"', now - timedelta(days=60), 3, "text/plain"),
        WebDAVItem(copied_path, "neu-kopiert.txt", False, '"b"', now - timedelta(days=20), len(copied_body), "text/plain"),
    ]
    FakeWebDAVClient.payloads[copied_path] = copied_body

    context = await collect_lesson_context(db, target, settings, now=now + timedelta(minutes=1))

    assert [item["filename"] for item in context["materials"]] == ["neu-kopiert.txt"]


@pytest.mark.asyncio
async def test_large_metadata_file_is_streamed_and_context_is_bounded(monkeypatch, db):
    target, now = _lesson_pair(db)
    path = "/ges/grosse-praesentation.txt"
    body = ("Recht und Gerechtigkeit im zerbrochnen Krug.\n" * 5000).encode()
    FakeWebDAVClient.items = [
        WebDAVItem(path, "grosse-praesentation.txt", False, '"large"', now, 261_814_666, "text/plain")
    ]
    FakeWebDAVClient.payloads = {path: body}
    monkeypatch.setattr("app.services.plugin_briefing.WebDAVClient", FakeWebDAVClient)
    settings = replace(
        get_settings(),
        iserv_webdav_url="https://iserv.example/webdav",
        iserv_username="user",
        iserv_password="secret",
        max_document_chars=2_000,
        plugin_max_context_chars=2_000,
    )

    context = await collect_lesson_context(db, target, settings, now=now)

    assert context["materials"][0]["filename"] == "grosse-praesentation.txt"
    assert len(context["materials"][0]["content"]) < 2_200
    assert "Kontextgrenze" in context["materials"][0]["content"]
    assert context["material_issues"] == []


def test_plugin_endpoint_requires_bearer_key(client):
    response = client.post(
        "/mcp",
        headers={"Accept": "application/json, text/event-stream"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1.0"},
            },
        },
    )
    assert response.status_code == 401
    assert "resource_metadata" in response.headers["www-authenticate"]
