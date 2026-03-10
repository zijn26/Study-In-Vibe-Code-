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


LESSON_TEMPLATE = """# {concept_name}

**Lĩnh vực:** {domain}
**Tags:** {tags}
**Độ phức tạp:** {complexity}
**Ngày tổng hợp:** {date}

---

{content_body}

---
## Lịch sử học
{learning_journey}
"""

# Global memory to track synthesis progress
# Format: { concept_id: { "status": "synthesizing", "current_chunk": 0, "total_chunks": 5, "message": "Planning..." } }
synthesis_progress = {}

PLANNING_SYSTEM_PROMPT = """Bạn là một Chuyên gia Kỹ thuật Senior (Principal Engineer) với khả năng sư phạm tuyệt vời.
Nhiệm vụ của bạn là nhận các thông tin rời rạc mà tôi thu thập được trong quá trình code,
sau đó lên DÀN Ý để tổng hợp thành một bài học hoàn chỉnh.

Quy tắc:
1. Xác định đây là một khái niệm nhỏ (thể hiện trong 1 đoạn ngắn) hay lớn (cần giải thích bằng hệ thống/nhiều đoạn).
2. Lập dàn ý bài học. Một bài học chuẩn cần có: TL;DR, Mental Model (Ví dụ đời thực), Cơ chế hoạt động (How it works), Code ví dụ có comment, và Best Practices/Anti-patterns (Khi nào cấm dùng).
3. ĐÁNH GIÁ: Trả về JSON chứa `is_large_topic` (boolean), `tags`, `complexity` và `outline`.

Format JSON:
{
  "is_large_topic": true/false,
  "tags": "#react #hooks",
  "complexity": "★★★☆☆",
  "outline": ["1. TL;DR", "2. Mental Model", "3. ..."]
}"""

SYNTHESIZE_CHUNK_PROMPT = """Bạn là một Senior Engineer cực giỏi hướng dẫn cho Junior.
Nhiệm vụ: Viết bài học dạng Markdown DỰA TRÊN Outline được cung cấp.

Quy tắc:
1. TRẢ VỀ HOÀN TOÀN LÀ MARKDOWN (KHÔNG PHẢI JSON). Không bọc trong ```markdown...```.
2. Nếu `is_large_topic` = false, HÃY VIẾT TOÀN BỘ BÀI HỌC VÀO LÚC NÀY.
3. Nếu `is_large_topic` = true, và bạn ĐƯỢC CHỈ ĐỊNH viết một phần cụ thể (Current Chunk), CHỈ ĐƯỢC VIẾT phần đó.
4. Lối văn: tự nhiên, dễ hiểu, dùng ngôn ngữ lập trình thực tế. Luôn đưa code mẫu nếu cần, và giải thích rõ ràng TẠI SAO lại code vậy. Không nói lan man.
"""


# Removed SYNTHESIZE_SYSTEM_PROMPT. Evaluated by chunks via SYNTHESIZE_CHUNK_PROMPT.


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


async def call_ai_api(system_prompt: str, user_prompt: str, force_json: bool = True):
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
    }
    if force_json:
        payload["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{config.AI_API_URL}/chat/completions",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]

        # Parse JSON only if forced
        if not force_json:
            return content

        import json
        try:
            return json.loads(content)
        except json.JSONDecodeError:
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

    plan_prompt = f"""Khái niệm: **{concept.name}**
Lĩnh vực: {concept.domain or 'general'}
Keywords: {', '.join(concept.keywords)}
Các ngữ cảnh thu thập ({len(marks)} lần):
{marks_summary}

Hãy đánh giá xem bài học này nên viết ngắn gọn hay viết thành nhiều đoạn dài (is_large_topic). 
Và cung cấp dàn ý outline dựa trên best practices (Mental Model, How it works, Best Practices)."""

    try:
        synthesis_progress[concept_id] = {
            "status": "synthesizing",
            "name": concept.name,
            "current_chunk": 0,
            "total_chunks": 1, 
            "message": "Đang phân tích dữ liệu và lên dàn ý..."
        }
        
        # 1. Ask for a plan
        plan = await call_ai_api(PLANNING_SYSTEM_PROMPT, plan_prompt, force_json=True)
        is_large = plan.get("is_large_topic", False)
        tags = plan.get("tags", "")
        complexity = plan.get("complexity", "★★★☆☆")
        outline = plan.get("outline", [])
        
        outline_text = "\n".join([f"- {o}" for o in outline])
        
        # 2. Execute synthesis (Single turn vs Multi-turn)
        full_content_body = ""
        
        if not is_large or len(outline) == 0:
            # Single turn execution
            synthesis_progress[concept_id]["message"] = "Đang tổng hợp nội dung bài học..."
            synthesis_progress[concept_id]["current_chunk"] = 1
            synthesis_progress[concept_id]["total_chunks"] = 1
            
            user_msg = f"""Hãy viết toàn bộ bài học Markdown cho: {concept.name}.
Dàn ý mong muốn:
{outline_text}
Dữ liệu tóm tắt thu được:
{marks_summary}"""
            full_content_body = await call_ai_api(SYNTHESIZE_CHUNK_PROMPT, user_msg, force_json=False)
        else:
            # Multi-turn execution by chunk
            accumulated_text = ""
            for idx, chunk_title in enumerate(outline):
                user_msg = f"""Bạn đang viết bài học: {concept.name}.
Dàn ý chung:
{outline_text}

Các nội dung ĐÃ viết (để bạn giữ tính nhất quán): 
{accumulated_text[-1000:] if len(accumulated_text) > 1000 else accumulated_text}

=> YÊU CẦU LƯỢT NÀY: Hãy chỉ viết tiếp hoàn chỉnh phần **{chunk_title}** dựa trên hiểu biết của bạn và dữ liệu thu thập ({marks_summary}). Không chào hỏi. Chỉ nhả Markdown."""
                
                chunk_resp = await call_ai_api(SYNTHESIZE_CHUNK_PROMPT, user_msg, force_json=False)
                accumulated_text += f"\n\n{chunk_resp}"
                full_content_body = accumulated_text
                
                synthesis_progress[concept_id]["current_chunk"] = idx + 1
                synthesis_progress[concept_id]["total_chunks"] = len(outline)
                synthesis_progress[concept_id]["message"] = f"Hoàn thành phần {idx+1}/{len(outline)}"
                print(f"[HOCAI] Synthesized chunk {idx+1}/{len(outline)} cho {concept.name}")

        # 3. Build finalize lesson formatting
        now = datetime.now(timezone.utc)
        learning_journey = "\n".join([
            f"| {m.timestamp.strftime('%Y-%m-%d') if m.timestamp else 'N/A'} | "
            f"{m.question_type} | +{m.final_score:.1f} |"
            for m in marks
        ])

        final_markdown = LESSON_TEMPLATE.format(
            concept_name=concept.name,
            domain=concept.domain or "general",
            tags=tags,
            complexity=complexity,
            date=now.strftime("%Y-%m-%d"),
            content_body=full_content_body,
            learning_journey=f"| Ngày | Sự kiện | Điểm |\n|------|---------|------|\n{learning_journey}",
        )

        # Save lesson
        context_hashes = [sha256_hash(m.context_snippet) for m in marks if m.context_snippet]
        lesson_name = concept.name

        lesson = Lesson(
            concept_id=concept.id,
            lesson_name=lesson_name,
            content=final_markdown,
            context_hashes=context_hashes,
        )
        db.add(lesson)

        # Update concept status to LEARNED
        concept.status = "LEARNED"
        db.commit()

        if concept_id in synthesis_progress:
            synthesis_progress[concept_id]["status"] = "done"
            synthesis_progress[concept_id]["message"] = "Tổng hợp thành công!"

        print(f"[OK] Lesson synthesized: {lesson_name}")
        return lesson

    except Exception as e:
        print(f"[ERROR] Synthesize failed for '{concept.name}': {e}")
        db.rollback()
        if concept_id in synthesis_progress:
            synthesis_progress[concept_id]["status"] = "error"
            synthesis_progress[concept_id]["message"] = f"Lỗi: {str(e)}"
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
        result = await call_ai_api(UPDATE_SYSTEM_PROMPT, user_prompt, force_json=True)

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
