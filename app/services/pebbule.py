from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger("classbrief")


class PebbuleError(RuntimeError):
    pass


@dataclass(frozen=True)
class PebbuleLesson:
    subject: str
    start: datetime
    end: datetime
    status: str
    kind: str
    room: str | None = None
    teacher: str | None = None
    note: str | None = None


def normalize_subject(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"))


def configured_subject_map(settings: Settings) -> dict[str, str]:
    try:
        value = json.loads(settings.pebbule_subject_map or "{}")
    except json.JSONDecodeError:
        logger.warning("pebbule_subject_map_invalid")
        return {}
    return {normalize_subject(str(key)): str(target) for key, target in value.items()} if isinstance(value, dict) else {}


class PebbuleClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def fetch_schedule(self, start: date, end: date) -> list[PebbuleLesson]:
        if not self.settings.pebbule_api_url or not self.settings.pebbule_integration_secret:
            raise PebbuleError("Pebbule ist nicht eingerichtet")
        url = self.settings.pebbule_api_url.rstrip("/") + "/api/v1/integrations/classbrief/schedule"
        try:
            response = httpx.get(
                url,
                params={"from": start.isoformat(), "to": end.isoformat()},
                headers={"Authorization": f"Bearer {self.settings.pebbule_integration_secret}"},
                timeout=15,
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PebbuleError("Pebbule-Stundenplan ist vorübergehend nicht erreichbar") from exc
        if payload.get("status") != "ok" or not isinstance(payload.get("lessons"), list):
            raise PebbuleError("Pebbule hat keinen gültigen Stundenplan geliefert")
        lessons: list[PebbuleLesson] = []
        for item in payload["lessons"]:
            try:
                start_at = datetime.fromisoformat(str(item["start"]).replace("Z", "+00:00"))
                end_at = datetime.fromisoformat(str(item["end"]).replace("Z", "+00:00"))
                subject = str(item["subject"]).strip()
            except (KeyError, TypeError, ValueError):
                continue
            if not subject or end_at <= start_at:
                continue
            lessons.append(PebbuleLesson(
                subject=subject,
                start=start_at,
                end=end_at,
                status=str(item.get("status") or "scheduled"),
                kind=str(item.get("kind") or "normal"),
                room=str(item["room"]).strip() if item.get("room") else None,
                teacher=str(item["teacher"]).strip() if item.get("teacher") else None,
                note=str(item["note"]).strip()[:500] if item.get("note") else None,
            ))
        return lessons
