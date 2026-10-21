from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"
AUDIO_DIR = DATA_DIR / "audio"
MODEL_DIR = DATA_DIR / "models"
WORK_DIR = DATA_DIR / "work"
DB_PATH = DATA_DIR / "clipper.db"
LOG_PATH = DATA_DIR / "clipper.log"

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv"}
MAX_UPLOAD_GB = 20
MAX_UPLOAD_BYTES = MAX_UPLOAD_GB * 1024**3

MAX_CLIP_SECONDS = 600
MAX_CLIPS_PER_REQUEST = 20

# --- Transcription (Stage 2) ---
WHISPER_MODEL = "medium.en"       # English-only. Alternatives: small.en | small | medium | large-v3
WHISPER_DEVICE = "auto"           # "auto" = try GPU, fall back to CPU | "cpu" | "cuda"
WHISPER_GPU_COMPUTE = "int8_float16"
WHISPER_CPU_COMPUTE = "int8"
WHISPER_LANGUAGE = "en"           # None = auto-detect (needs a multilingual model)

# --- Local language model via Ollama (Stage 3) ---
OLLAMA_URL = "http://127.0.0.1:11434"
LLM_MODEL = "qwen3:4b"            # must be a LOCAL model (no ":cloud" tags)
LLM_CONTEXT = 6144                # tokens of memory per request; bigger = more VRAM

# --- Vertical output (Stage 4) ---
VERTICAL_WIDTH = 1080
VERTICAL_HEIGHT = 1920

for _d in (DATA_DIR, UPLOAD_DIR, OUTPUT_DIR, AUDIO_DIR, MODEL_DIR, WORK_DIR):
    _d.mkdir(parents=True, exist_ok=True)