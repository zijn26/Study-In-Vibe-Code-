"""
HOCAI Server — Configuration
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file if exists
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

# Database
BASE_DIR = Path(__file__).parent
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'hocai.db'}")

# AI API (OpenAI-compatible)
AI_API_URL = os.getenv("AI_API_URL", "http://localhost:11434/v1")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "gpt-3.5-turbo")

# Server
HOCAI_PORT = int(os.getenv("HOCAI_PORT", "8000"))
HOCAI_HOST = os.getenv("HOCAI_HOST", "127.0.0.1")

# Scoring thresholds
SCORE_GLIMPSED_MAX = 10
SCORE_LEARNING_MAX = 20
SCORE_CONSOLIDATING_MAX = 30
SCORE_READY_THRESHOLD = 30

# Limits
MAX_CONCEPTS_PER_MARK = 3
MAX_RELATED_LESSONS = 5
