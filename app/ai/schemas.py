from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Term(StrictModel):
    term: str
    explanation: str


class SourceRef(StrictModel):
    file: str
    pages: list[int]


class TaskRef(StrictModel):
    task: str
    source: SourceRef


class KeyValue(StrictModel):
    label: str
    value: str


class BriefBlock(StrictModel):
    """A safe presentation primitive chosen by the model and rendered by ClassBrief."""

    type: Literal["heading", "paragraph", "bullets", "numbered", "checklist", "callout", "key_value"]
    title: str = ""
    text: str = ""
    items: list[str] = Field(default_factory=list)
    rows: list[KeyValue] = Field(default_factory=list)
    tone: Literal["neutral", "important", "confirmed", "uncertain", "speak"] = "neutral"


class BriefPresentation(StrictModel):
    quick: list[BriefBlock] = Field(default_factory=list)
    medium: list[BriefBlock] = Field(default_factory=list)
    deep: list[BriefBlock] = Field(default_factory=list)


class AnswerCard(StrictModel):
    question: str
    answer: str
    evidence: list[str] = Field(default_factory=list)
    certainty: Literal["confirmed", "likely", "uncertain"] = "likely"


class ClarificationOption(StrictModel):
    id: str
    label: str


class ClarificationQuestion(StrictModel):
    id: str
    prompt: str
    why_it_matters: str = ""
    options: list[ClarificationOption] = Field(min_length=2, max_length=5)


class ClarificationSet(StrictModel):
    questions: list[ClarificationQuestion] = Field(default_factory=list, max_length=3)


class MaterialAnalysisSchema(StrictModel):
    title: str
    document_type: str
    main_topics: list[str]
    key_points: list[str]
    important_terms: list[Term]
    tasks: list[TaskRef]
    likely_relevance: Literal["low", "medium", "high"]
    sources: list[SourceRef]


class NamedMaterialAnalysis(StrictModel):
    filename: str
    analysis: MaterialAnalysisSchema


class MaterialBatchSchema(StrictModel):
    materials: list[NamedMaterialAnalysis]


class DeepBrief(StrictModel):
    summary: str
    prior_knowledge: list[str]
    new_materials: list[str]
    important_terms: list[Term]
    likely_tasks: list[str]
    pitfalls: list[str]
    sources: list[SourceRef]
    connections: list[str] = Field(default_factory=list)
    answer_cards: list[AnswerCard] = Field(default_factory=list)
    presentation: BriefPresentation | None = None


class LessonBriefingSchema(StrictModel):
    probable_topic: str
    quick_brief: str
    medium_brief: str
    deep: DeepBrief


class SubjectMemorySchema(StrictModel):
    current_unit: str
    recent_topics: list[str]
    important_concepts: list[Term]
    current_transition: str
    open_questions: list[str]
    last_confirmed_at: str
