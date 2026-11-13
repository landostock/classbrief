from __future__ import annotations

import json
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.ai.base import AIProvider, AIResult
from app.ai.prompts import BRIEFING_INSTRUCTIONS, CLARIFICATION_INSTRUCTIONS, MATERIAL_INSTRUCTIONS, MEMORY_INSTRUCTIONS
from app.ai.schemas import ClarificationSet, LessonBriefingSchema, MaterialAnalysisSchema, MaterialBatchSchema, SubjectMemorySchema
from app.config import Settings

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class AIProviderError(RuntimeError):
    pass


class OpenAIProvider(AIProvider):
    endpoint = "https://api.openai.com/v1/responses"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def _structured(self, *, schema: type[SchemaT], name: str, instructions: str, content: str, model: str) -> AIResult:
        if not self.settings.openai_api_key:
            raise AIProviderError("OPENAI_API_KEY ist nicht konfiguriert")
        payload = {
            "model": model,
            "instructions": instructions,
            "input": content[: self.settings.max_ai_input_chars],
            "store": False,
            "text": {"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema.model_json_schema()}},
        }
        last_error: Exception | None = None
        for attempt in range(2):
            current = payload if attempt == 0 else {**payload, "input": content + "\n\nDie vorherige Ausgabe war ungültig. Gib ausschließlich gültiges JSON nach dem Schema zurück."}
            async with httpx.AsyncClient(timeout=90, verify=True) as client:
                response = await client.post(self.endpoint, headers={"Authorization": f"Bearer {self.settings.openai_api_key}"}, json=current)
            if response.status_code >= 400:
                raise AIProviderError(f"OpenAI-Anfrage fehlgeschlagen ({response.status_code})")
            body = response.json()
            try:
                raw = self._output_text(body)
                value = schema.model_validate_json(raw)
                usage = body.get("usage") or {}
                return AIResult(value, body.get("model", model), int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)))
            except (ValidationError, json.JSONDecodeError, KeyError, TypeError) as exc:
                last_error = exc
        raise AIProviderError("KI_ERROR: Strukturierte Antwort war auch nach Reparatur ungültig") from last_error

    @staticmethod
    def _output_text(body: dict) -> str:
        for item in body.get("output", []):
            if item.get("type") == "message":
                for part in item.get("content", []):
                    if part.get("type") == "output_text":
                        return part["text"]
        raise KeyError("output_text")

    async def analyze_material(self, filename: str, text: str) -> AIResult:
        return await self._structured(
            schema=MaterialAnalysisSchema,
            name="material_analysis",
            instructions=MATERIAL_INSTRUCTIONS,
            content=f"DATEI: {filename}\n\nINHALT:\n{text}",
            model=self.settings.openai_model,
        )

    async def analyze_materials(self, documents: list[tuple[str, str]]) -> AIResult:
        content = "\n\n".join(f"=== DATEI: {filename} ===\n{text}" for filename, text in documents)
        return await self._structured(
            schema=MaterialBatchSchema,
            name="material_batch_analysis",
            instructions=MATERIAL_INSTRUCTIONS + " Analysiere jede gelieferte Datei getrennt und gib für jede exakt den Dateinamen zurück.",
            content=content,
            model=self.settings.openai_model,
        )

    async def create_clarifications(self, subject: str, memory: dict, analyses: list[dict]) -> AIResult:
        content = json.dumps({"fach": subject, "fachgedaechtnis": memory, "materialanalysen": analyses}, ensure_ascii=False)
        return await self._structured(schema=ClarificationSet, name="briefing_clarifications", instructions=CLARIFICATION_INSTRUCTIONS, content=content, model=self.settings.openai_model)

    async def create_lesson_briefing(self, subject: str, memory: dict, analyses: list[dict], deep: bool = False, clarifications: list[dict] | None = None) -> AIResult:
        model = self.settings.openai_reasoning_model if deep and self.settings.openai_reasoning_model else self.settings.openai_model
        content = json.dumps({"fach": subject, "fachgedaechtnis": memory, "materialanalysen": analyses, "bestaetigte_rueckfragen": clarifications or []}, ensure_ascii=False)
        return await self._structured(schema=LessonBriefingSchema, name="lesson_briefing", instructions=BRIEFING_INSTRUCTIONS, content=content, model=model)

    async def update_subject_memory(self, current: dict, briefing: dict, confirmed_at: str) -> AIResult:
        content = json.dumps({"bisher": current, "bestaetigtes_briefing": briefing, "bestaetigt_am": confirmed_at}, ensure_ascii=False)
        return await self._structured(schema=SubjectMemorySchema, name="subject_memory", instructions=MEMORY_INSTRUCTIONS, content=content, model=self.settings.openai_model)
