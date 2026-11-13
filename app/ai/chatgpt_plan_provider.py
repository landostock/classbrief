from __future__ import annotations

import json
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.ai.base import AIProvider, AIResult
from app.ai.openai_provider import AIProviderError
from app.ai.prompts import BRIEFING_INSTRUCTIONS, MATERIAL_INSTRUCTIONS, MEMORY_INSTRUCTIONS
from app.ai.schemas import LessonBriefingSchema, MaterialAnalysisSchema, MaterialBatchSchema, SubjectMemorySchema
from app.config import Settings
from app.models import ChatGPTConnection
from app.services.chatgpt_plan import ChatGPTPlanError, access_token

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class ChatGPTPlanProvider(AIProvider):
    endpoint = "https://api.openai.com/v1/responses"
    models_endpoint = "https://api.openai.com/v1/models"

    def __init__(self, db: Session, settings: Settings, connection: ChatGPTConnection):
        self.db = db
        self.settings = settings
        self.connection = connection

    async def _model(self, token: str) -> str:
        if self.connection.selected_model:
            return self.connection.selected_model
        try:
            async with httpx.AsyncClient(timeout=30, verify=True) as client:
                response = await client.get(self.models_endpoint, headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
            body = response.json()
            items = body.get("models") or body.get("data") or []
            slugs = [str(item.get("slug") or item.get("id")) for item in items if item.get("visibility", "list") == "list"]
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise AIProviderError("Verfügbare ChatGPT-Modelle konnten nicht geladen werden") from exc
        requested = self.settings.chatgpt_plan_model
        selected = requested if requested and requested in slugs else (slugs[0] if slugs else "")
        if not selected:
            raise AIProviderError("Für dieses ChatGPT-Konto ist kein Modell verfügbar")
        self.connection.selected_model = selected
        self.db.commit()
        return selected

    async def _request(self, *, token: str, payload: dict) -> tuple[str, dict]:
        chunks: list[str] = []
        completed: dict | None = None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=30), verify=True) as client:
                async with client.stream(
                    "POST",
                    self.endpoint,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json=payload,
                ) as response:
                    if response.status_code >= 400:
                        body = (await response.aread()).decode(errors="replace")[:500]
                        raise AIProviderError(f"ChatGPT-Anfrage fehlgeschlagen ({response.status_code}): {body}")
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw or raw == "[DONE]":
                            continue
                        event = json.loads(raw)
                        event_type = event.get("type")
                        if event_type == "response.output_text.delta":
                            chunks.append(str(event.get("delta") or ""))
                        elif event_type == "response.completed":
                            completed = event.get("response") or {}
                        elif event_type == "response.failed":
                            error = (event.get("response") or {}).get("error") or {}
                            raise AIProviderError(str(error.get("message") or error.get("code") or "ChatGPT-Anfrage fehlgeschlagen"))
        except AIProviderError:
            raise
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
            raise AIProviderError("Die ChatGPT-Verbindung wurde während der Antwort unterbrochen") from exc
        if completed is None:
            raise AIProviderError("ChatGPT hat die Antwort nicht vollständig abgeschlossen")
        if not chunks:
            for item in completed.get("output", []):
                for part in item.get("content", []):
                    if part.get("type") == "output_text":
                        chunks.append(str(part.get("text") or ""))
        return "".join(chunks), completed

    async def _structured(self, *, schema: type[SchemaT], name: str, instructions: str, content: str) -> AIResult:
        try:
            token = await access_token(self.db, self.settings, self.connection)
        except ChatGPTPlanError as exc:
            raise AIProviderError(str(exc)) from exc
        model = await self._model(token)
        last_error: Exception | None = None
        for attempt in range(2):
            request_content = content[: self.settings.max_ai_input_chars]
            if attempt:
                request_content += "\n\nDie vorherige Ausgabe war ungültig. Gib ausschließlich gültiges JSON nach dem Schema zurück."
            payload = {
                "model": model,
                "instructions": instructions,
                "input": [{"role": "user", "content": request_content}],
                "store": False,
                "stream": True,
                "text": {"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema.model_json_schema()}},
            }
            raw, completed = await self._request(token=token, payload=payload)
            try:
                value = schema.model_validate_json(raw)
                usage = completed.get("usage") or {}
                return AIResult(value, str(completed.get("model") or model), int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)))
            except (ValidationError, json.JSONDecodeError, TypeError) as exc:
                last_error = exc
        raise AIProviderError("ChatGPT lieferte auch nach einer Reparatur keine gültige strukturierte Antwort") from last_error

    async def analyze_material(self, filename: str, text: str) -> AIResult:
        return await self._structured(
            schema=MaterialAnalysisSchema,
            name="material_analysis",
            instructions=MATERIAL_INSTRUCTIONS,
            content=f"DATEI: {filename}\n\nINHALT:\n{text}",
        )

    async def analyze_materials(self, documents: list[tuple[str, str]]) -> AIResult:
        content = "\n\n".join(f"=== DATEI: {filename} ===\n{text}" for filename, text in documents)
        return await self._structured(
            schema=MaterialBatchSchema,
            name="material_batch_analysis",
            instructions=MATERIAL_INSTRUCTIONS + " Analysiere jede gelieferte Datei getrennt und gib für jede exakt den Dateinamen zurück.",
            content=content,
        )

    async def create_lesson_briefing(self, subject: str, memory: dict, analyses: list[dict], deep: bool = False) -> AIResult:
        content = json.dumps({"fach": subject, "fachgedaechtnis": memory, "materialanalysen": analyses}, ensure_ascii=False)
        return await self._structured(schema=LessonBriefingSchema, name="lesson_briefing", instructions=BRIEFING_INSTRUCTIONS, content=content)

    async def update_subject_memory(self, current: dict, briefing: dict, confirmed_at: str) -> AIResult:
        content = json.dumps({"bisher": current, "bestaetigtes_briefing": briefing, "bestaetigt_am": confirmed_at}, ensure_ascii=False)
        return await self._structured(schema=SubjectMemorySchema, name="subject_memory", instructions=MEMORY_INSTRUCTIONS, content=content)
