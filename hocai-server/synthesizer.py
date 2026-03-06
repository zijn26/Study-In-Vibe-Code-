"""
HOCAI Server — AI Synthesizer
Calls OpenAI-compatible API to synthesize and update lessons.
"""
import hashlib
import httpx
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from models import Concept, ConceptMark, Lesson
import config


def sha256_hash(text: str) -> str:
    """Generate SHA-256 hash of text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ─── Lesson Template ───

LESSON_TEMPLATE = """# {concept_name}

**Lĩnh vực:** {domain}
**Tags:** {tags}
**Độ phức tạp:** {complexity}
**Ngày tổng hợp:** {date}
**Trạng thái:** LEARNED

---

## Tóm tắt
{summary}

## Giải thích chi tiết
{detailed_explanation}

## Ví dụ thực tế
{examples}

## Kiến thức liên quan
{related_knowledge}

## Hành trình học
{learning_journey}
"""


SYNTHESIZE_SYSTEM_PROMPT = """Bạn là một giáo viên kỹ thuật chuyên tổng hợp kiến thức.
Nhiệm vụ: Tạo bài học TỔNG QUAN về một khái niệm kỹ thuật.

QUY TẮC QUAN TRỌNG:
1. Mỗi bài học chỉ về MỘT chủ đề tổng quan (VD: "PWM là gì, nguyên lý, code")
2. KHÔNG gộp nhiều chủ đề vào một bài
3. Bài học phải BAO QUÁT: định nghĩa, cơ chế hoạt động, cách dùng, code ví dụ với nhiều framework
4. Viết bằng tiếng Việt, dễ hiểu
5. Trả về theo format JSON:
{
  "summary": "Tóm tắt 2-4 câu",
  "detailed_explanation": "Giải thích chi tiết...",
  "examples": "Code ví dụ với nhiều framework...",
  "related_knowledge": "- [PREREQUISITE] → ... \\n- [RELATED] → ...",
  "complexity": "★★★☆☆ (3/5)",
  "tags": "#tag1 #tag2 #tag3"
}"""


UPDATE_SYSTEM_PROMPT = """Bạn là một giáo viên kỹ thuật.
Nhiệm vụ: Đánh giá xem nội dung mới có BỔ SUNG GIÁ TRỊ cho bài học đã có không.

QUY TẮC:
1. Chỉ bổ sung nếu nội dung mới mang: góc nhìn mới, framework mới, use case mới
2. KHÔNG bổ sung nếu chỉ là lặp lại hoặc quá nhỏ lẻ
3. KHÔNG làm bài học bị rối — nội dung bổ sung phải lồng ghép tự nhiên

Trả về JSON:
{
  "should_update": true/false,
  "reason": "Lý do",
  "addition": "Nội dung bổ sung (nếu should_update=true)"
}"""


async def call_ai_api(system_prompt: str, user_prompt: str) -> dict:
    """Call OpenAI-compatible API."""
    headers = {"Content-Type": "application/json"}
    if config.AI_API_KEY:
        headers["Authorization"] = f"Bearer {config.AI_API_KEY}"

    payload = {
        "model": config.AI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{config.AI_API_URL}/chat/completions",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]

        # Parse JSON from response
        import json
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code block
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
                return json.loads(content)
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
                return json.loads(content)
            raise


async def synthesize_lesson(concept_id: int, db: Session):
    """
    Synthesize a new lesson when concept reaches READY status.
    Calls AI API to generate comprehensive lesson content.
    """
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if not concept:
        return

    marks = (
        db.query(ConceptMark)
        .filter(ConceptMark.concept_id == concept_id)
        .order_by(ConceptMark.timestamp)
        .all()
    )

    # Build user prompt with all context
    marks_summary = "\n".join([
        f"- [{m.question_type}] {m.context_snippet}"
        for m in marks
        if m.context_snippet
    ])

    user_prompt = f"""Tổng hợp bài học TỔNG QUAN về: **{concept.name}**
Lĩnh vực: {concept.domain or 'general'}
Keywords: {', '.join(concept.keywords)}

Các ngữ cảnh đã thu thập ({len(marks)} lần):
{marks_summary}

Hãy tạo bài học BAO QUÁT về khái niệm này (không phải về cách dùng cụ thể)."""

    try:
        result = await call_ai_api(SYNTHESIZE_SYSTEM_PROMPT, user_prompt)

        # Build lesson content from template
        now = datetime.now(timezone.utc)
        learning_journey = "\n".join([
            f"| {m.timestamp.strftime('%Y-%m-%d') if m.timestamp else 'N/A'} | "
            f"{m.question_type} | +{m.final_score:.1f} |"
            for m in marks
        ])

        content = LESSON_TEMPLATE.format(
            concept_name=concept.name,
            domain=concept.domain or "general",
            tags=result.get("tags", ""),
            complexity=result.get("complexity", "★★★☆☆ (3/5)"),
            date=now.strftime("%Y-%m-%d"),
            summary=result.get("summary", ""),
            detailed_explanation=result.get("detailed_explanation", ""),
            examples=result.get("examples", ""),
            related_knowledge=result.get("related_knowledge", ""),
            learning_journey=f"| Ngày | Sự kiện | Điểm |\n|------|---------|------|\n{learning_journey}",
        )

        # Save lesson
        context_hashes = [sha256_hash(m.context_snippet) for m in marks if m.context_snippet]
        lesson_name = concept.name

        lesson = Lesson(
            concept_id=concept.id,
            lesson_name=lesson_name,
            content=content,
            context_hashes=context_hashes,
        )
        db.add(lesson)

        # Update concept status to LEARNED
        concept.status = "LEARNED"
        db.commit()

        print(f"[OK] Lesson synthesized: {lesson_name}")
        return lesson

    except Exception as e:
        print(f"[ERROR] Synthesize failed for '{concept.name}': {e}")
        db.rollback()
        return None


async def maybe_update_lesson(
    concept: Concept,
    context_snippet: str,
    db: Session,
) -> bool:
    """
    Check if a LEARNED concept's lesson should be updated with new context.
    Only updates if AI determines the new content adds genuine value.
    Returns True if lesson was updated.
    """
    lesson = db.query(Lesson).filter(Lesson.concept_id == concept.id).first()
    if not lesson:
        return False

    # Check duplicate via hash
    new_hash = sha256_hash(context_snippet)
    if new_hash in lesson.context_hashes:
        return False  # Already seen

    # Ask AI if this is worth adding
    user_prompt = f"""Bài học hiện tại về: **{concept.name}**

NỘI DUNG BÀI HỌC HIỆN TẠI:
{lesson.content[:2000]}

NỘI DUNG MỚI CẦN ĐÁNH GIÁ:
{context_snippet}

Nội dung mới này có bổ sung giá trị thực sự cho bài học không?"""

    try:
        result = await call_ai_api(UPDATE_SYSTEM_PROMPT, user_prompt)

        if result.get("should_update", False):
            addition = result.get("addition", "")
            if addition:
                # Append to lesson
                now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                lesson.content += f"\n\n---\n## Bổ sung ({now_str})\n{addition}"
                lesson.version += 1
                lesson.updated_at = datetime.now(timezone.utc)

                # Update hashes
                hashes = lesson.context_hashes
                hashes.append(new_hash)
                lesson.context_hashes = hashes

                db.commit()
                print(f"[OK] Lesson updated: {lesson.lesson_name} (v{lesson.version})")
                return True

        return False

    except Exception as e:
        print(f"[ERROR] Update check failed for '{concept.name}': {e}")
        return False
