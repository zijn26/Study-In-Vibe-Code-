"""
HOCAI Server — Pydantic schemas for request/response validation
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


# ─── Request schemas ───

class RelatedConcept(BaseModel):
    name: str
    relation: str = Field(description="DIRECT | RELATED | INDIRECT")


class MarkRequest(BaseModel):
    timestamp: str
    project_name: str = "global"
    root_concept: str
    keywords: list[str] = []
    domain: str = ""
    question_type: str = "HOWTO"  # CONCEPT | COMPARE | HOWTO | DEBUG | CONFIRM
    base_score: float = 5.0
    related_concepts: list[RelatedConcept] = []
    context_snippet: str = ""


class SearchLessonsRequest(BaseModel):
    keywords: list[str] = []
    question: str = ""
    limit: int = 5


class SettingsRequest(BaseModel):
    api_url: str
    api_key: str
    model: str


class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: list[ChatMessage]


# ─── Response schemas ───

class RelatedLessonResponse(BaseModel):
    concept: str
    lesson_id: int
    relation: str
    summary: str


class MarkResponse(BaseModel):
    status: str  # "marked" | "skipped" | "updated"
    concept: str
    current_score: float
    concept_status: str
    related_lessons: list[RelatedLessonResponse] = []


class SearchLessonsResponse(BaseModel):
    found: bool
    lessons: list[RelatedLessonResponse] = []


class ConceptResponse(BaseModel):
    id: int
    name: str
    domain: Optional[str]
    status: str
    accumulated_score: float
    mark_count: int
    keywords: list[str]
    first_seen: Optional[str]
    last_seen: Optional[str]
    has_lesson: bool = False


class ConceptDetailResponse(ConceptResponse):
    marks: list[dict] = []
    edges: list[dict] = []


class LessonResponse(BaseModel):
    id: int
    concept_name: str
    lesson_name: str
    content: str
    version: int
    synthesized_at: Optional[str]
    updated_at: Optional[str]


class LessonListResponse(BaseModel):
    id: int
    concept_name: str
    lesson_name: str
    version: int
    summary: str  # first 200 chars


class StatsResponse(BaseModel):
    total_concepts: int
    total_marks: int
    total_lessons: int
    concepts_by_status: dict[str, int]
    recent_concepts: list[dict] = []
