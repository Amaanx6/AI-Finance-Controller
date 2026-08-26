import os
from pathlib import Path
from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(ENV_FILE)

PROVIDER = os.environ.get("PROVIDER", "auto").strip().lower()
if PROVIDER not in ("groq", "gemini", "auto", "local"):
    raise ValueError(f"Invalid PROVIDER '{PROVIDER}' — must be 'groq', 'gemini', 'auto', or 'local'")

DEV_MODE = os.environ.get("DEV_MODE", "false").strip().lower() == "true"

# --- LOCAL SETUP (Ollama) ---
LOCAL_API_BASE = os.environ.get("LOCAL_API_BASE", "http://localhost:11434/v1")
LOCAL_MODEL_NAME = os.environ.get("LOCAL_MODEL_NAME", "qwen2.5:7b")

# --- GROQ SETUP ---
GROQ_MODEL_NAME = "openai/gpt-oss-20b"
GROQ_TPM_BUDGET = 7000
GROQ_MAX_TOKENS = 1200

# --- GEMINI SETUP ---
GEMINI_MODEL_NAME = "gemini-2.5-flash"
GEMINI_RPM_BUDGET = 15
GEMINI_MAX_TOKENS = 2000
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

MAX_TURNS = 8

# --- MULTI-PROVIDER DYNAMIC KEY POOL ---
KEY_POOL = []

if PROVIDER == "local":
    KEY_POOL.append(("local", "ollama", LOCAL_MODEL_NAME))

elif PROVIDER == "groq":
    for k, v in os.environ.items():
        if k.startswith("GROQ_API_KEY") and v.strip():
            KEY_POOL.append(("groq", v.strip(), GROQ_MODEL_NAME))
    if not KEY_POOL and os.environ.get("GROQ_API_KEY"):
        KEY_POOL.append(("groq", os.environ.get("GROQ_API_KEY").strip(), GROQ_MODEL_NAME))

elif PROVIDER == "gemini":
    for k, v in os.environ.items():
        if k.startswith("GEMINI_API_KEY") and v.strip():
            KEY_POOL.append(("gemini", v.strip(), GEMINI_MODEL_NAME))
    if not KEY_POOL and os.environ.get("GEMINI_API_KEY"):
        KEY_POOL.append(("gemini", os.environ.get("GEMINI_API_KEY").strip(), GEMINI_MODEL_NAME))

elif PROVIDER == "auto":
    # Hybrid fallback pool: load all detected cloud keys
    for k, v in os.environ.items():
        if k.startswith("GROQ_API_KEY") and v.strip():
            KEY_POOL.append(("groq", v.strip(), GROQ_MODEL_NAME))
    for k, v in os.environ.items():
        if k.startswith("GEMINI_API_KEY") and v.strip():
            KEY_POOL.append(("gemini", v.strip(), GEMINI_MODEL_NAME))

if not KEY_POOL:
    raise ValueError(f"No valid API keys configured for PROVIDER='{PROVIDER}'. Check your .env file.")

if DEV_MODE:
    print(f"[i] DEV_MODE active — using pool with {len(KEY_POOL)} key(s).")