"""
HOCAI Server — Scoring Engine
Computes final_score = base_score × multiplier × novelty × time_decay
"""
from math import exp
from datetime import datetime, timezone
from config import (
    SCORE_GLIMPSED_MAX,
    SCORE_LEARNING_MAX,
    SCORE_CONSOLIDATING_MAX,
    SCORE_READY_THRESHOLD,
)

# Question type multipliers
QUESTION_MULTIPLIER = {
    "CONCEPT": 1.5,
    "COMPARE": 1.2,
    "HOWTO":   1.0,
    "DEBUG":   0.8,
    "CONFIRM": 0.6,
}

# Graph propagation coefficients
RELATION_PROPAGATION = {
    "DIRECT":   0.6,
    "RELATED":  0.3,
    "INDIRECT": 0.1,
}


def compute_novelty(concept) -> float:
    """
    Novelty factor based on concept status.
    - New concept: 1.0 (full value)
    - Learning/Consolidating: 0.3 (partial)
    - Learned: 0.0 (skip scoring, may still update lesson)
    """
    if concept is None:
        return 1.0
    if concept.status == "LEARNED":
        return 0.0
    return 0.3


def compute_time_decay(concept) -> float:
    """
    Time decay: full value for first 7 days, then exponential decay.
    """
    if concept is None:
        return 1.0
    now = datetime.now(timezone.utc)
    first_seen = concept.first_seen
    if first_seen.tzinfo is None:
        first_seen = first_seen.replace(tzinfo=timezone.utc)
    days = (now - first_seen).days
    if days <= 7:
        return 1.0
    return exp(-0.1 * days)


def compute_final_score(base_score: float, question_type: str, concept) -> float:
    """
    Final score = base_score × question_multiplier × novelty × time_decay
    """
    multiplier = QUESTION_MULTIPLIER.get(question_type, 1.0)
    novelty = compute_novelty(concept)
    time_decay = compute_time_decay(concept)
    return round(base_score * multiplier * novelty * time_decay, 2)


def determine_status(accumulated_score: float, current_status: str) -> str:
    """
    Determine concept status based on accumulated score.
    Once LEARNED, stays LEARNED (no regression).
    """
    if current_status == "LEARNED":
        return "LEARNED"

    if accumulated_score > SCORE_READY_THRESHOLD:
        return "READY"
    elif accumulated_score > SCORE_LEARNING_MAX:
        return "CONSOLIDATING"
    elif accumulated_score >= SCORE_GLIMPSED_MAX:
        return "LEARNING"
    else:
        return "GLIMPSED"
