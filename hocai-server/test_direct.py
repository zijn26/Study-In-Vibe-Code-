import asyncio
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Concept, ConceptMark
from synthesizer import synthesize_lesson
import config

async def test_direct_synthesize():
    engine = create_engine('sqlite:///d:/HOCAISYSTEM/hocai-server/hocai.db')
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    # create a strong concept
    concept = db.query(Concept).filter(Concept.name == "Direct Nextjs").first()
    if not concept:
        concept = Concept(name="Direct Nextjs", status="READY", domain="Frontend", accumulated_score=35, mark_count=5)
        db.add(concept)
        db.commit()
        db.refresh(concept)
        
        for i in range(3):
            mark = ConceptMark(
                concept_id=concept.id,
                context_snippet=f"Đoạn {i} nói về React Server Component rat quy chuan, khac biet hoan toan voi client.",
                question_type="CONCEPT",
                base_score=10,
                final_score=10
            )
            db.add(mark)
        db.commit()

    print(f"Triggering sync for concept_id={concept.id} ('{concept.name}')")
    lesson = await synthesize_lesson(concept.id, db)
    
    if lesson:
        print("====== SYNTHESIS OK ======")
        print(lesson.content[:1000])
    else:
        print("SYNTHESIS FAILED (Check console error!)")

    db.close()

if __name__ == "__main__":
    asyncio.run(test_direct_synthesize())
