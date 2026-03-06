"""
HOCAI Server — SQLAlchemy ORM Models
4 tables: concepts, concept_marks, concept_edges, lessons
Concepts are SHARED across projects (UNIQUE name).
"""
import json
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Float, Text, DateTime, ForeignKey,
    Index, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from database import Base


def utcnow():
    return datetime.now(timezone.utc)


class Concept(Base):
    __tablename__ = "concepts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, unique=True)  # SHARED across projects
    keywords_json = Column("keywords", Text, default="[]")   # JSON array (SQLite)
    domain = Column(String(100), nullable=True)
    status = Column(String(20), default="GLIMPSED")
    accumulated_score = Column(Float, default=0.0)
    first_seen = Column(DateTime, default=utcnow)
    last_seen = Column(DateTime, default=utcnow)
    mark_count = Column(Integer, default=0)

    # Relationships
    marks = relationship("ConceptMark", back_populates="concept", cascade="all, delete-orphan")
    lesson = relationship("Lesson", back_populates="concept", uselist=False)

    @property
    def keywords(self):
        try:
            return json.loads(self.keywords_json) if self.keywords_json else []
        except (json.JSONDecodeError, TypeError):
            return []

    @keywords.setter
    def keywords(self, value):
        self.keywords_json = json.dumps(value, ensure_ascii=False)

    def __repr__(self):
        return f"<Concept(name='{self.name}', status='{self.status}', score={self.accumulated_score:.1f})>"


# Indexes for concepts
Index("idx_concepts_status", Concept.status)
Index("idx_concepts_domain", Concept.domain)


class ConceptMark(Base):
    __tablename__ = "concept_marks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    concept_id = Column(Integer, ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False)
    project_name = Column(String(200), nullable=False, default="global")  # Track source project
    timestamp = Column(DateTime, default=utcnow)
    question_type = Column(String(20))
    base_score = Column(Float)
    final_score = Column(Float)
    context_snippet = Column(Text)

    # Relationships
    concept = relationship("Concept", back_populates="marks")

    def __repr__(self):
        return f"<Mark(concept_id={self.concept_id}, score={self.final_score:.1f}, project='{self.project_name}')>"


Index("idx_marks_concept", ConceptMark.concept_id)
Index("idx_marks_project", ConceptMark.project_name)


class ConceptEdge(Base):
    __tablename__ = "concept_edges"

    concept_a = Column(Integer, ForeignKey("concepts.id"), primary_key=True)
    concept_b = Column(Integer, ForeignKey("concepts.id"), primary_key=True)
    relation_type = Column(String(20))
    strength = Column(Float, default=1.0)

    def __repr__(self):
        return f"<Edge({self.concept_a} -> {self.concept_b}, type='{self.relation_type}')>"


Index("idx_edges_a", ConceptEdge.concept_a)
Index("idx_edges_b", ConceptEdge.concept_b)


class Lesson(Base):
    __tablename__ = "lessons"

    id = Column(Integer, primary_key=True, autoincrement=True)
    concept_id = Column(Integer, ForeignKey("concepts.id"), unique=True)  # 1 concept = 1 lesson
    lesson_name = Column(String(300))
    content = Column(Text, nullable=False)
    synthesized_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)
    version = Column(Integer, default=1)
    context_hashes_json = Column("context_hashes", Text, default="[]")  # JSON array of sha256

    # Relationships
    concept = relationship("Concept", back_populates="lesson")

    @property
    def context_hashes(self):
        try:
            return json.loads(self.context_hashes_json) if self.context_hashes_json else []
        except (json.JSONDecodeError, TypeError):
            return []

    @context_hashes.setter
    def context_hashes(self, value):
        self.context_hashes_json = json.dumps(value)

    def __repr__(self):
        return f"<Lesson(name='{self.lesson_name}', version={self.version})>"


Index("idx_lessons_concept", Lesson.concept_id)
