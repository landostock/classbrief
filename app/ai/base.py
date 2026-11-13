from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.ai.schemas import LessonBriefingSchema, MaterialAnalysisSchema, MaterialBatchSchema, SubjectMemorySchema


@dataclass(frozen=True)
class AIResult:
    value: MaterialAnalysisSchema | MaterialBatchSchema | LessonBriefingSchema | SubjectMemorySchema
    model: str
    input_tokens: int
    output_tokens: int


class AIProvider(ABC):
    @abstractmethod
    async def analyze_material(self, filename: str, text: str) -> AIResult: ...

    @abstractmethod
    async def analyze_materials(self, documents: list[tuple[str, str]]) -> AIResult: ...

    @abstractmethod
    async def create_lesson_briefing(self, subject: str, memory: dict, analyses: list[dict], deep: bool = False) -> AIResult: ...

    @abstractmethod
    async def update_subject_memory(self, current: dict, briefing: dict, confirmed_at: str) -> AIResult: ...
