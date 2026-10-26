import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_PATH = DATA_DIR / "settings.json"

# Options the Settings page can change: name -> (default, kind, rule)
#   choice: rule = allowed values | int: rule = (min, max) | text: rule = max length | path: full folder path
SETTINGS = {
    "WHISPER_MODEL": ("medium.en", "choice",
                      ["small.en", "medium.en", "small", "medium", "large-v3", "distil-large-v3"]),
    "WHISPER_DEVICE": ("auto", "choice", ["auto", "cpu", "cuda"]),
    "WHISPER_GPU_COMPUTE": ("int8_float16", "choice", ["float16", "int8_float16", "int8"]),
    "WHISPER_LANGUAGE": ("en", "text", 10),
    "LLM_MODEL": ("qwen3:4b", "text", 80),
    "OLLAMA_URL": ("http://127.0.0.1:11434", "text", 200),
    "LLM_CONTEXT": (6144, "int", (2048, 32768)),
    "VERTICAL_SIZE": ("1080x1920", "choice", ["1080x1920", "720x1280"]),
    "MAX_UPLOAD_GB": (20, "int", (1, 200)),
    "MODEL_DIR": (str(DATA_DIR / "models"), "path", None),
}


def _coerce(name, value):
    default, kind, rule = SETTINGS[name]
    if kind == "choice":
        if value not in rule:
            raise ValueError(f"{name} must be one of: {', '.join(rule)}.")
        return value
    if kind == "int":
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be a whole number.")
        if not rule[0] <= number <= rule[1]:
            raise ValueError(f"{name} must be between {rule[0]} and {rule[1]}.")
        return number

    text = str(value if value is not None else "").strip()
    if len(text) > (rule or 300):
        raise ValueError(f"{name} is too long.")
    if name == "LLM_MODEL" and (not text or "cloud" in text.lower()):
        raise ValueError("The AI model must be a local Ollama model (not a :cloud one).")
    if name == "OLLAMA_URL" and not text.startswith(("http://", "https://")):
        raise ValueError("The Ollama address must start with http:// or https://")
    if name == "MODEL_DIR" and not (text and Path(text).is_absolute()):
        raise ValueError("The model folder must be a full path, for example C:\\Projects\\data\\models")
    return text


def validate_settings(raw):
    """Check settings from the Settings page. Raises ValueError with a readable message."""
    return {name: _coerce(name, raw[name]) for name in SETTINGS if name in raw}


def load_saved():
    """Settings saved in data/settings.json. Anything missing or invalid is ignored."""
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    saved = {}
    for name in SETTINGS:
        if isinstance(data, dict) and name in data:
            try:
                saved[name] = _coerce(name, data[name])
            except ValueError:
                pass
    return saved


def save_settings(values):
    SETTINGS_PATH.write_text(json.dumps(values, indent=2), encoding="utf-8")


_overrides = load_saved()


def _get(name):
    return _overrides.get(name, SETTINGS[name][0])


def current_settings():
    """The settings this running app started with."""
    return {name: _get(name) for name in SETTINGS}


# ---------- folders ----------
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"
AUDIO_DIR = DATA_DIR / "audio"
MODEL_DIR = Path(_get("MODEL_DIR"))
WORK_DIR = DATA_DIR / "work"
DB_PATH = DATA_DIR / "clipper.db"
LOG_PATH = DATA_DIR / "clipper.log"

# ---------- uploads and clips ----------
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv"}
MAX_UPLOAD_GB = _get("MAX_UPLOAD_GB")
MAX_UPLOAD_BYTES = MAX_UPLOAD_GB * 1024**3
MAX_CLIP_SECONDS = 600
MAX_CLIPS_PER_REQUEST = 20

# ---------- transcription ----------
WHISPER_MODEL = _get("WHISPER_MODEL")
WHISPER_DEVICE = _get("WHISPER_DEVICE")
WHISPER_GPU_COMPUTE = _get("WHISPER_GPU_COMPUTE")
WHISPER_CPU_COMPUTE = "int8"
WHISPER_LANGUAGE = _get("WHISPER_LANGUAGE") or None   # empty = detect automatically

# ---------- local language model via Ollama ----------
OLLAMA_URL = _get("OLLAMA_URL")
LLM_MODEL = _get("LLM_MODEL")
LLM_CONTEXT = _get("LLM_CONTEXT")

# ---------- vertical output ----------
_width, _height = _get("VERTICAL_SIZE").split("x")
VERTICAL_WIDTH = int(_width)
VERTICAL_HEIGHT = int(_height)

for _d in (DATA_DIR, UPLOAD_DIR, OUTPUT_DIR, AUDIO_DIR, MODEL_DIR, WORK_DIR):
    _d.mkdir(parents=True, exist_ok=True)