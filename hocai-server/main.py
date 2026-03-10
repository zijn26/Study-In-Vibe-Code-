"""
HOCAI Server — FastAPI Application & Routes
Main entry point for the HOCAI knowledge tracking system.
"""
import os
import json
from pathlib import Path
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db, init_db
from models import Concept, ConceptMark, ConceptEdge, Lesson
from schemas import (
    MarkRequest, MarkResponse,
    SearchLessonsRequest, SearchLessonsResponse,
    ConceptResponse, ConceptDetailResponse,
    LessonResponse, LessonListResponse,
    LessonResponse, LessonListResponse,
    StatsResponse, RelatedLessonResponse,
    SettingsRequest, ChatRequest
)
from scoring import compute_final_score, determine_status
import config
from graph import (
    find_or_create_concept,
    propagate_scores,
    get_related_lessons,
    upsert_edge,
)
from synthesizer import sha256_hash, synthesize_lesson, maybe_update_lesson, call_ai_api, synthesis_progress
from config import HOCAI_PORT, HOCAI_HOST, MAX_RELATED_LESSONS
from mcp_service import get_mcp_tools, execute_mcp_tool



@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database on startup."""
    print("[HOCAI] Server starting...")
    init_db()
    print("[HOCAI] Database initialized")
    yield
    print("[HOCAI] Server shutting down")


app = FastAPI(
    title="HOCAI Server",
    description="He thong hoc kien thuc tu dong -- Knowledge tracking & synthesis",
    version="2.1.0",
    lifespan=lifespan,
)

# CORS for dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static dashboard
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/dashboard", StaticFiles(directory=str(STATIC_DIR), html=True), name="dashboard")


# ─── POST /api/mark ───

@app.post("/api/mark", response_model=MarkResponse)
async def mark_concept(
    req: MarkRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Receive extraction from Antigravity agent.
    Scores, stores, propagates, and returns related lessons.
    """
    # Step 1: Find or create concept (shared, no project scoping)
    concept = db.query(Concept).filter(Concept.name == req.root_concept).first()
    is_new = concept is None

    if is_new:
        concept = Concept(
            name=req.root_concept,
            keywords=req.keywords,
            domain=req.domain,
            status="GLIMPSED",
            accumulated_score=0.0,
            mark_count=0,
        )
        db.add(concept)
        db.flush()

    # Step 2: Handle LEARNED concepts
    if concept.status == "LEARNED":
        if req.context_snippet:
            new_hash = sha256_hash(req.context_snippet)
            lesson = db.query(Lesson).filter(Lesson.concept_id == concept.id).first()

            if lesson and new_hash not in lesson.context_hashes:
                # New context for LEARNED concept — trigger async update check
                background_tasks.add_task(
                    _run_update_lesson, concept.id, req.context_snippet
                )

                # Still record the mark for logging (score=0)
                mark = ConceptMark(
                    concept_id=concept.id,
                    project_name=req.project_name,
                    question_type=req.question_type,
                    base_score=req.base_score,
                    final_score=0.0,
                    context_snippet=req.context_snippet,
                )
                db.add(mark)
                concept.mark_count += 1
                concept.last_seen = datetime.now(timezone.utc)
                db.commit()

                related = get_related_lessons(concept.id, db, MAX_RELATED_LESSONS)
                return MarkResponse(
                    status="updated",
                    concept=concept.name,
                    current_score=concept.accumulated_score,
                    concept_status=concept.status,
                    related_lessons=[RelatedLessonResponse(**r) for r in related],
                )
            else:
                # Already seen this context
                related = get_related_lessons(concept.id, db, MAX_RELATED_LESSONS)
                return MarkResponse(
                    status="skipped",
                    concept=concept.name,
                    current_score=concept.accumulated_score,
                    concept_status=concept.status,
                    related_lessons=[RelatedLessonResponse(**r) for r in related],
                )

    # Step 3: Compute score for non-LEARNED concepts
    final_score = compute_final_score(req.base_score, req.question_type, concept if not is_new else None)

    # Step 4: Record mark
    mark = ConceptMark(
        concept_id=concept.id,
        project_name=req.project_name,
        question_type=req.question_type,
        base_score=req.base_score,
        final_score=final_score,
        context_snippet=req.context_snippet,
    )
    db.add(mark)

    # Step 5: Update concept
    concept.accumulated_score += final_score
    concept.mark_count += 1
    concept.last_seen = datetime.now(timezone.utc)

    # Merge keywords (union)
    existing_kw = set(concept.keywords)
    existing_kw.update(req.keywords)
    concept.keywords = list(existing_kw)

    if req.domain and not concept.domain:
        concept.domain = req.domain

    # Step 6: Update status
    old_status = concept.status
    concept.status = determine_status(concept.accumulated_score, concept.status)

    # Step 7: Propagate scores to related concepts
    related_data = [{"name": rc.name, "relation": rc.relation} for rc in req.related_concepts]
    if related_data:
        propagate_scores(concept.id, final_score, related_data, req.domain, db)

    db.commit()

    # Step 8: Trigger synthesis if status just became READY
    if concept.status == "READY" and old_status != "READY":
        background_tasks.add_task(_run_synthesize, concept.id)

    # Step 9: Get related lessons
    related = get_related_lessons(concept.id, db, MAX_RELATED_LESSONS)

    return MarkResponse(
        status="marked",
        concept=concept.name,
        current_score=concept.accumulated_score,
        concept_status=concept.status,
        related_lessons=[RelatedLessonResponse(**r) for r in related],
    )


# ─── POST /api/search-lessons ───

@app.post("/api/search-lessons", response_model=SearchLessonsResponse)
async def search_lessons(req: SearchLessonsRequest, db: Session = Depends(get_db)):
    """
    Search for existing lessons by keywords.
    Used by Antigravity agent BEFORE answering to show reminders.
    """
    if not req.keywords:
        return SearchLessonsResponse(found=False, lessons=[])

    # Find LEARNED concepts with matching keywords
    learned_concepts = (
        db.query(Concept)
        .filter(Concept.status == "LEARNED")
        .all()
    )

    results = []
    for concept in learned_concepts:
        # Check keyword overlap
        concept_kw = set(kw.lower() for kw in concept.keywords)
        search_kw = set(kw.lower() for kw in req.keywords)
        overlap = concept_kw & search_kw

        if overlap:
            lesson = db.query(Lesson).filter(Lesson.concept_id == concept.id).first()
            if lesson:
                relation = "DIRECT" if len(overlap) >= 2 else "RELATED"
                results.append({
                    "concept": concept.name,
                    "lesson_id": lesson.id,
                    "relation": relation,
                    "summary": (lesson.content[:150] + "...") if len(lesson.content) > 150 else lesson.content,
                    "_overlap": len(overlap),  # for sorting
                })

    # Sort by relevance (more overlap = more relevant)
    results.sort(key=lambda x: (-x["_overlap"], 0 if x["relation"] == "DIRECT" else 1))

    # Clean up and limit
    clean_results = [
        RelatedLessonResponse(
            concept=r["concept"],
            lesson_id=r["lesson_id"],
            relation=r["relation"],
            summary=r["summary"],
        )
        for r in results[:req.limit]
    ]

    return SearchLessonsResponse(
        found=len(clean_results) > 0,
        lessons=clean_results,
    )


# ─── GET /api/concepts ───

@app.get("/api/concepts", response_model=list[ConceptResponse])
async def list_concepts(
    status: str | None = None,
    db: Session = Depends(get_db),
):
    """List all concepts, optionally filtered by status."""
    query = db.query(Concept)
    if status:
        query = query.filter(Concept.status == status)
    concepts = query.order_by(Concept.accumulated_score.desc()).all()

    return [
        ConceptResponse(
            id=c.id,
            name=c.name,
            domain=c.domain,
            status=c.status,
            accumulated_score=c.accumulated_score,
            mark_count=c.mark_count,
            keywords=c.keywords,
            first_seen=c.first_seen.isoformat() if c.first_seen else None,
            last_seen=c.last_seen.isoformat() if c.last_seen else None,
            has_lesson=c.lesson is not None,
        )
        for c in concepts
    ]


# ─── GET /api/concepts/{id} ───

@app.get("/api/concepts/{concept_id}", response_model=ConceptDetailResponse)
async def get_concept(concept_id: int, db: Session = Depends(get_db)):
    """Get detailed info about a concept including marks and edges."""
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if not concept:
        raise HTTPException(status_code=404, detail="Concept not found")

    marks = (
        db.query(ConceptMark)
        .filter(ConceptMark.concept_id == concept_id)
        .order_by(ConceptMark.timestamp.desc())
        .all()
    )

    edges = (
        db.query(ConceptEdge, Concept)
        .join(Concept, Concept.id == ConceptEdge.concept_b)
        .filter(ConceptEdge.concept_a == concept_id)
        .all()
    )

    return ConceptDetailResponse(
        id=concept.id,
        name=concept.name,
        domain=concept.domain,
        status=concept.status,
        accumulated_score=concept.accumulated_score,
        mark_count=concept.mark_count,
        keywords=concept.keywords,
        first_seen=concept.first_seen.isoformat() if concept.first_seen else None,
        last_seen=concept.last_seen.isoformat() if concept.last_seen else None,
        has_lesson=concept.lesson is not None,
        marks=[
            {
                "id": m.id,
                "project": m.project_name,
                "type": m.question_type,
                "base_score": m.base_score,
                "final_score": m.final_score,
                "timestamp": m.timestamp.isoformat() if m.timestamp else None,
                "snippet": (m.context_snippet[:100] + "...") if m.context_snippet and len(m.context_snippet) > 100 else m.context_snippet,
            }
            for m in marks
        ],
        edges=[
            {
                "target": c.name,
                "relation": e.relation_type,
                "strength": e.strength,
            }
            for e, c in edges
        ],
    )


# ─── DELETE /api/concepts/{id} ───

@app.delete("/api/concepts/{concept_id}")
async def delete_concept(concept_id: int, db: Session = Depends(get_db)):
    """Delete a concept and all its associated marks, edges, and lessons."""
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if not concept:
        raise HTTPException(status_code=404, detail="Concept not found")
    
    # Explicitly delete dependent records to avoid SQLite FK constraints
    # if the DB schema wasn't migrated with ON DELETE CASCADE
    db.query(ConceptMark).filter(ConceptMark.concept_id == concept_id).delete()
    db.query(ConceptEdge).filter(
        (ConceptEdge.concept_a == concept_id) | (ConceptEdge.concept_b == concept_id)
    ).delete()
    db.query(Lesson).filter(Lesson.concept_id == concept_id).delete()
    
    db.delete(concept)
    db.commit()
    return {"status": "success", "message": f"Deleted concept '{concept.name}'"}


# ─── GET /api/lessons ───

@app.get("/api/lessons", response_model=list[LessonListResponse])
async def list_lessons(db: Session = Depends(get_db)):
    """List all synthesized lessons."""
    lessons = (
        db.query(Lesson, Concept)
        .join(Concept, Concept.id == Lesson.concept_id)
        .order_by(Lesson.updated_at.desc())
        .all()
    )

    return [
        LessonListResponse(
            id=lesson.id,
            concept_name=concept.name,
            lesson_name=lesson.lesson_name or concept.name,
            version=lesson.version,
            summary=(lesson.content[:200] + "...") if len(lesson.content) > 200 else lesson.content,
        )
        for lesson, concept in lessons
    ]


# ─── GET /api/lessons/{id} ───

@app.get("/api/lessons/{lesson_id}", response_model=LessonResponse)
async def get_lesson(lesson_id: int, db: Session = Depends(get_db)):
    """Get full lesson content."""
    lesson = db.query(Lesson).filter(Lesson.id == lesson_id).first()
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    concept = db.query(Concept).filter(Concept.id == lesson.concept_id).first()

    return LessonResponse(
        id=lesson.id,
        concept_name=concept.name if concept else "Unknown",
        lesson_name=lesson.lesson_name or (concept.name if concept else "Unknown"),
        content=lesson.content,
        version=lesson.version,
        synthesized_at=lesson.synthesized_at.isoformat() if lesson.synthesized_at else None,
        updated_at=lesson.updated_at.isoformat() if lesson.updated_at else None,
    )


# ─── POST /api/lessons/{id}/resynthesize ───

@app.post("/api/lessons/{lesson_id}/resynthesize")
async def resynthesize_lesson(
    lesson_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Force re-synthesis of an existing lesson."""
    lesson = db.query(Lesson).filter(Lesson.id == lesson_id).first()
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    
    concept = db.query(Concept).filter(Concept.id == lesson.concept_id).first()
    if not concept:
        raise HTTPException(status_code=404, detail="Concept not found")

    concept.status = "READY"
    db.delete(lesson)
    db.commit()

    background_tasks.add_task(_run_synthesize, concept.id)
    return {"status": "triggered", "message": f"Re-synthesis started for '{concept.name}'"}


# ─── POST /api/synthesize/{id} ───

@app.post("/api/synthesize/{concept_id}")
async def trigger_synthesize(
    concept_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Manually trigger lesson synthesis for a concept."""
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if not concept:
        raise HTTPException(status_code=404, detail="Concept not found")

    if concept.status == "LEARNED":
        return {"status": "already_learned", "message": f"'{concept.name}' already has a lesson"}

    background_tasks.add_task(_run_synthesize, concept.id)
    return {"status": "triggered", "message": f"Synthesis started for '{concept.name}'"}


# ─── GET /api/progress ───

@app.get("/api/progress")
async def get_progress():
    """Return current ongoing synthesis global progress state."""
    return synthesis_progress


# ─── GET /api/stats ───

@app.get("/api/stats", response_model=StatsResponse)
async def get_stats(db: Session = Depends(get_db)):
    """Get overall system statistics."""
    total_concepts = db.query(func.count(Concept.id)).scalar() or 0
    total_marks = db.query(func.count(ConceptMark.id)).scalar() or 0
    total_lessons = db.query(func.count(Lesson.id)).scalar() or 0

    # Count by status
    status_counts = (
        db.query(Concept.status, func.count(Concept.id))
        .group_by(Concept.status)
        .all()
    )
    by_status = {status: count for status, count in status_counts}

    # Recent concepts (last 10)
    recent = (
        db.query(Concept)
        .order_by(Concept.last_seen.desc())
        .limit(10)
        .all()
    )

    return StatsResponse(
        total_concepts=total_concepts,
        total_marks=total_marks,
        total_lessons=total_lessons,
        concepts_by_status=by_status,
        recent_concepts=[
            {
                "name": c.name,
                "status": c.status,
                "score": c.accumulated_score,
                "last_seen": c.last_seen.isoformat() if c.last_seen else None,
            }
            for c in recent
        ],
    )


@app.get("/")
async def root_redirect():
    return RedirectResponse(url="/dashboard/index.html")


@app.get("/api/health")
async def health_check():
    return {
        "service": "HOCAI Server",
        "version": "2.1.0",
        "status": "running",
    }


def update_env_file(key: str, value: str):
    """Update or add a key-value pair in the .env file."""
    env_path = Path(__file__).parent / ".env"
    
    # Read existing lines
    lines = []
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
    # Process lines or append new
    key_found = False
    new_lines = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}\n")
            key_found = True
        else:
            new_lines.append(line)
            
    if not key_found:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"
        new_lines.append(f"{key}={value}\n")
        
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


# ─── GET & POST /api/settings ───

@app.get("/api/settings")
async def get_settings():
    return {
        "api_url": config.AI_API_URL,
        "api_key": config.AI_API_KEY,
        "model": config.AI_MODEL,
        "brightdata_mcp_url": os.environ.get("BRIGHTDATA_MCP_URL", ""),
        "brightdata_api_key": os.environ.get("BRIGHTDATA_API_KEY", ""),
    }

class SettingsRequestWithMCP(SettingsRequest):
    brightdata_mcp_url: str | None = None
    brightdata_api_key: str | None = None

@app.post("/api/settings")
async def update_settings(req: SettingsRequestWithMCP):
    # Update in-memory
    config.AI_API_URL = req.api_url
    config.AI_API_KEY = req.api_key
    config.AI_MODEL = req.model
    
    if req.brightdata_mcp_url is not None:
        os.environ["BRIGHTDATA_MCP_URL"] = req.brightdata_mcp_url
    if req.brightdata_api_key is not None:
        os.environ["BRIGHTDATA_API_KEY"] = req.brightdata_api_key

    # Update .env file
    update_env_file("AI_API_URL", req.api_url)
    update_env_file("AI_API_KEY", req.api_key)
    update_env_file("AI_MODEL", req.model)
    if req.brightdata_mcp_url is not None:
        update_env_file("BRIGHTDATA_MCP_URL", req.brightdata_mcp_url)
    if req.brightdata_api_key is not None:
        update_env_file("BRIGHTDATA_API_KEY", req.brightdata_api_key)

    return {"status": "success", "message": "Settings updated"}

# ─── POST /api/chat ───

from fastapi.responses import StreamingResponse
import httpx

@app.post("/api/chat")
async def chat_with_ai(req: ChatRequest):
    """Proxy chat requests to the configured AI API via streaming."""
    headers = {"Content-Type": "application/json"}
    if config.AI_API_KEY:
        headers["Authorization"] = f"Bearer {config.AI_API_KEY}"

    payload = {
        "model": config.AI_MODEL,
        "messages": [msg.dict() for msg in req.messages],
        "temperature": 0.7,
        "stream": True
    }

    async def event_generator():
        MAX_TOOL_CALLS = 6
        tool_call_count = 0
        messages = [msg.dict() for msg in req.messages]

        while True:
            # Refresh payload
            payload = {
                "model": config.AI_MODEL,
                "messages": messages,
                "temperature": 0.7,
                "stream": True
            }
            
            # Fetch tools
            tools = await get_mcp_tools()
            if tools and tool_call_count < MAX_TOOL_CALLS:
                payload["tools"] = tools

            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    async with client.stream(
                        "POST",
                        f"{config.AI_API_URL}/chat/completions",
                        json=payload,
                        headers=headers,
                    ) as response:
                        response.raise_for_status()
                        
                        is_tool_call = False
                        tool_calls_buffer = {}

                        async for line in response.aiter_lines():
                            if not line:
                                continue
                            
                            if line.startswith("data: "):
                                data_str = line[6:].strip()
                                if data_str == "[DONE]":
                                    continue
                                
                                try:
                                    data = json.loads(data_str)
                                    if "choices" in data and len(data["choices"]) > 0:
                                        delta = data["choices"][0].get("delta", {})
                                        
                                        # Detect tool calls
                                        if "tool_calls" in delta:
                                            is_tool_call = True
                                            for tc in delta["tool_calls"]:
                                                idx = tc["index"]
                                                if idx not in tool_calls_buffer:
                                                    tool_calls_buffer[idx] = {
                                                        "id": tc.get("id", ""), 
                                                        "type": "function", 
                                                        "function": {"name": "", "arguments": ""}
                                                    }
                                                
                                                # merge name
                                                if "function" in tc and "name" in tc["function"] and tc["function"]["name"]:
                                                    tool_calls_buffer[idx]["function"]["name"] += tc["function"]["name"]
                                                # merge arguments
                                                if "function" in tc and "arguments" in tc["function"] and tc["function"]["arguments"]:
                                                    tool_calls_buffer[idx]["function"]["arguments"] += tc["function"]["arguments"]
                                        
                                        # Yield text if not tool call
                                        elif not is_tool_call and "content" in delta and delta["content"]:
                                            yield f"{line}\n"
                                except Exception as e:
                                    pass

            except Exception as e:
                print(f"[HOCAI] Chat streaming error: {e}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                break
                
            if is_tool_call:
                # Add the assistant message with tool calls
                tool_calls_list = [v for k,v in sorted(tool_calls_buffer.items())]
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls_list
                })
                
                # Execute tools sequentially
                for tc in tool_calls_list:
                    t_name = tc["function"]["name"]
                    t_args_str = tc["function"]["arguments"]
                    try:
                        t_args = json.loads(t_args_str)
                    except:
                        t_args = {}
                    
                    # Notify frontend
                    loading_msg = f"\n\n_[HOCAI] Đang dùng công cụ '{t_name}' qua BrightData..._\n\n"
                    yield f"data: {json.dumps({'choices': [{'delta': {'content': loading_msg}}]})}\n\n"
                    
                    # Execute via MCP
                    t_result = await execute_mcp_tool(t_name, t_args)
                    
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": t_name,
                        "content": str(t_result)
                    })
                
                tool_call_count += 1
                # Loop continues to send tools results back to AI
            else:
                # Streaming finished normally
                yield "data: [DONE]\n\n"
                break

    return StreamingResponse(event_generator(), media_type="text/event-stream")



# ─── Background task wrappers ───

async def _run_synthesize(concept_id: int):
    """Wrapper to run synthesize_lesson with a new DB session."""
    from database import SessionLocal
    db = SessionLocal()
    try:
        await synthesize_lesson(concept_id, db)
    finally:
        db.close()


async def _run_update_lesson(concept_id: int, context_snippet: str):
    """Wrapper to run maybe_update_lesson with a new DB session."""
    from database import SessionLocal
    db = SessionLocal()
    try:
        concept = db.query(Concept).filter(Concept.id == concept_id).first()
        if concept:
            await maybe_update_lesson(concept, context_snippet, db)
    finally:
        db.close()


# ─── Entry point ───

if __name__ == "__main__":
    import uvicorn
    print(f"[HOCAI] Starting HOCAI Server on {HOCAI_HOST}:{HOCAI_PORT}")
    uvicorn.run(app, host=HOCAI_HOST, port=HOCAI_PORT)
