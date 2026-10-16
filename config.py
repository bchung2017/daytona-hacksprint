import os

from dotenv import load_dotenv

load_dotenv()

PLAN_MODEL = os.getenv("PLAN_MODEL", "claude-fable-5-1")
FETCH_MODEL = os.getenv("FETCH_MODEL", "claude-opus-5-5")
MATCH_TOLERANCE = float(os.getenv("MATCH_TOLERANCE", "0.01"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
