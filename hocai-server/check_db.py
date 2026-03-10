import asyncio
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Lesson

def check_lesson():
    engine = create_engine('sqlite:///d:/HOCAISYSTEM/hocai-server/hocai.db')
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    
    lesson = db.query(Lesson).filter(Lesson.lesson_name == "Nextjs App Router").first()
    if lesson:
        print("====== LESSON FOUND ======")
        print(f"Length of lesson: {len(lesson.content)}")
        print(lesson.content[:1500])
        print("====== LESSON CHUNK END ======")
    else:
        print("Lesson 'Nextjs App Router' not found.")
        
    db.close()

if __name__ == "__main__":
    check_lesson()
