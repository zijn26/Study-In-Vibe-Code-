"""
HOCAI Server — Graph Propagation
Manages concept edges and propagates scores to related concepts.
"""
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from models import Concept, ConceptEdge
from scoring import RELATION_PROPAGATION, determine_status


def find_or_create_concept(name: str, domain: str, db: Session) -> Concept:
    """Find existing concept by name or create a new one."""
    concept = db.query(Concept).filter(Concept.name == name).first()
    if concept is None:
        concept = Concept(
            name=name,
            domain=domain,
            status="GLIMPSED",
            accumulated_score=0.0,
            mark_count=0,
        )
        db.add(concept)
        db.flush()  # Get the ID
    return concept


def upsert_edge(concept_a_id: int, concept_b_id: int, relation_type: str, db: Session):
    """Create or update an edge between two concepts."""
    if concept_a_id == concept_b_id:
        return  # No self-loops

    edge = db.query(ConceptEdge).filter(
        ConceptEdge.concept_a == concept_a_id,
        ConceptEdge.concept_b == concept_b_id,
    ).first()

    if edge is None:
        edge = ConceptEdge(
            concept_a=concept_a_id,
            concept_b=concept_b_id,
            relation_type=relation_type,
            strength=1.0,
        )
        db.add(edge)
    else:
        # Strengthen existing edge
        edge.strength = min(edge.strength + 0.5, 5.0)
        # Upgrade relation type if stronger
        priority = {"INDIRECT": 0, "RELATED": 1, "DIRECT": 2}
        if priority.get(relation_type, 0) > priority.get(edge.relation_type, 0):
            edge.relation_type = relation_type


def propagate_scores(
    concept_id: int,
    final_score: float,
    related_concepts: list[dict],
    domain: str,
    db: Session,
):
    """
    Propagate a fraction of the score to related concepts.
    Creates concepts and edges as needed.
    """
    for rc in related_concepts:
        neighbor = find_or_create_concept(rc["name"], domain, db)

        # Calculate propagated score
        propagation_factor = RELATION_PROPAGATION.get(rc["relation"], 0.1)
        propagated = round(final_score * propagation_factor, 2)

        # Only propagate to non-LEARNED concepts
        if neighbor.status != "LEARNED" and propagated > 0:
            neighbor.accumulated_score += propagated
            neighbor.last_seen = datetime.now(timezone.utc)
            # Update status based on new score
            neighbor.status = determine_status(
                neighbor.accumulated_score, neighbor.status
            )

        # Upsert edge in both directions
        upsert_edge(concept_id, neighbor.id, rc["relation"], db)
        # Reverse edge with same type
        upsert_edge(neighbor.id, concept_id, rc["relation"], db)


def get_related_lessons(concept_id: int, db: Session, limit: int = 5) -> list[dict]:
    """
    Get lessons of related concepts (those connected via edges and LEARNED).
    """
    from models import Lesson

    results = (
        db.query(Concept, Lesson, ConceptEdge)
        .join(ConceptEdge, ConceptEdge.concept_b == Concept.id)
        .join(Lesson, Lesson.concept_id == Concept.id)
        .filter(
            ConceptEdge.concept_a == concept_id,
            Concept.status == "LEARNED",
        )
        .order_by(ConceptEdge.strength.desc())
        .limit(limit)
        .all()
    )

    return [
        {
            "concept": concept.name,
            "lesson_id": lesson.id,
            "relation": edge.relation_type,
            "summary": (lesson.content[:150] + "...") if len(lesson.content) > 150 else lesson.content,
        }
        for concept, lesson, edge in results
    ]
