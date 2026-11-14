"""Central configuration. Every value can be overridden with an environment variable."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent
TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Kolkata"))

# Supabase Postgres connection string (Project Settings > Database > Connection string, "Session pooler").
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Login: portal admins arrive with their Supabase session; the service verifies it with Supabase.
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
SESSION_HOURS = int(os.getenv("SESSION_HOURS", "8"))
SECURE_COOKIES = os.getenv("SECURE_COOKIES", "1") == "1"
DEV_NO_AUTH = os.getenv("DEV_NO_AUTH", "0") == "1"       # local testing only

MODEL_NAME = os.getenv("MODEL_NAME", "buffalo_l")        # buffalo_s = faster, less accurate
DET_SIZE = int(os.getenv("DET_SIZE", "640"))             # detector input size
MATCH_THRESHOLD = float(os.getenv("MATCH_THRESHOLD", "0.45"))  # cosine similarity; raise = stricter
MIN_FACE_PX = int(os.getenv("MIN_FACE_PX", "60"))        # ignore faces smaller than this (px wide)

PROCESS_FPS = float(os.getenv("PROCESS_FPS", "5"))       # recognition runs at this rate per camera
PROCESS_MAX_WIDTH = int(os.getenv("PROCESS_MAX_WIDTH", "960"))  # frames are downscaled for AI

COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "600"))    # 10-minute anti-duplicate window

LIVENESS_ENABLED = os.getenv("LIVENESS_ENABLED", "1") == "1"
LIVENESS_MIN_SHARPNESS = float(os.getenv("LIVENESS_MIN_SHARPNESS", "40"))  # tune per camera

STREAM_MAX_WIDTH = int(os.getenv("STREAM_MAX_WIDTH", "960"))  # width of the video sent to the browser
