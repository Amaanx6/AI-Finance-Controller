import os
from dotenv import load_dotenv

load_dotenv()

# "groq" or "gemini" — dispatches which client call_llm_with_retry uses.
PROVIDER = os.environ.get("PROVIDER", "groq").strip().lower()
if PROVIDER not in ("groq", "gemini"):
    raise ValueError(f"Invalid PROVIDER '{PROVIDER}' — must be 'groq' or 'gemini'")

# --- Groq settings (unchanged behavior) ---
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL_NAME = "openai/gpt-oss-120b"
GROQ_TPM_BUDGET = 7000          # free-tier ~8k TPM ceiling, kept under with headroom
GROQ_MAX_TOKENS = 1024

# --- Gemini settings ---
# Using Gemini's OpenAI-compatible endpoint (see proposer.py comment for why).
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL_NAME = os.environ.get("GEMINI_MODEL_NAME", "gemini-3.6-flash")
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# Gemini free-tier limits are per-minute RPM + RPD, NOT a strict TPM meter like
# Groq's — but per-request token ceilings are much higher, so we throttle on
# request cadence rather than tokens. Verify current numbers at
# https://ai.google.dev/gemini-api/docs/rate-limits since Google revises these.
GEMINI_RPM_BUDGET = int(os.environ.get("GEMINI_RPM_BUDGET", "10"))
GEMINI_MAX_TOKENS = int(os.environ.get("GEMINI_MAX_TOKENS", "4096"))

MAX_TURNS = 4