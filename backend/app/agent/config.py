import os
from dotenv import load_dotenv

load_dotenv()

PROVIDER = os.environ.get("PROVIDER", "auto").strip().lower()
if PROVIDER not in ("groq", "gemini", "auto", "local"):
    raise ValueError(f"Invalid PROVIDER '{PROVIDER}' — must be 'groq', 'gemini', 'auto', or 'local'")

DEV_MODE = os.environ.get("DEV_MODE", "false").strip().lower() == "true"

# --- LOCAL SETUP ---
LOCAL_API_BASE = os.environ.get("LOCAL_API_BASE", "http://localhost:11434/v1")
LOCAL_MODEL_NAME = os.environ.get("LOCAL_MODEL_NAME", "qwen2.5:7b")

# --- GROQ SETUP ---
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL_NAME = "openai/gpt-oss-20b"
GROQ_TPM_BUDGET = 7000
GROQ_MAX_TOKENS = 1200
MAX_TURNS = 8

# --- MULTI-KEY POOL STRUCTURE ---
KEY_POOL = []

if PROVIDER == "local":
    KEY_POOL.append(("local", "ollama", LOCAL_MODEL_NAME))
else:
    for k, v in os.environ.items():
        if k.startswith("GROQ_API_KEY") and v.strip():
            KEY_POOL.append(("groq", v.strip(), GROQ_MODEL_NAME))

    if not KEY_POOL and GROQ_API_KEY:
        KEY_POOL.append(("groq", GROQ_API_KEY.strip(), GROQ_MODEL_NAME))

if not KEY_POOL:
    raise ValueError(f"No valid configuration found for PROVIDER='{PROVIDER}'.")

if DEV_MODE:
    print(f"[i] DEV_MODE active — using {GROQ_MODEL_NAME}.")