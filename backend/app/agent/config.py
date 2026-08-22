import os
from dotenv import load_dotenv

load_dotenv()

# "groq" or "gemini" — dispatches which client call_llm_with_retry uses.
PROVIDER = os.environ.get("PROVIDER", "auto").strip().lower()
if PROVIDER not in ("groq", "gemini", "auto"):
    raise ValueError(f"Invalid PROVIDER '{PROVIDER}' — must be 'groq', 'gemini', or 'auto'")
# "auto" is the real dual-provider mode (weighted toward Gemini — see
# reconciler.py's resolve_batch for why) and is now the default.

# DEV_MODE=true swaps in a faster, cheaper Groq model for quick iteration
# during development. Never use this for the actual evaluation run or demo —
# switch back to DEV_MODE=false for anything whose output/timing you're
# reporting as real evidence.
DEV_MODE = os.environ.get("DEV_MODE", "false").strip().lower() == "true"

# --- Groq settings ---
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

if DEV_MODE:
    # openai/gpt-oss-20b: same GPT-OSS family as the production model below,
    # but smaller, ~2x faster (1000 tok/sec vs 500), and cheaper — good for
    # fast iteration. NOTE: llama-3.1-8b-instant (the original obvious choice
    # for a "small fast model") was deprecated and shut down by Groq on
    # August 16, 2026 — do not use it, it will fail with a model-not-found
    # error. gpt-oss-20b is Groq's current recommended lightweight option.
    GROQ_MODEL_NAME = "openai/gpt-oss-20b"
else:
    # openai/gpt-oss-120b: the real model — use this for the actual
    # evaluation run (Prompt 5) and final demo, since that's what your
    # reported numbers should reflect.
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

MAX_TURNS = 6

if DEV_MODE:
    print(f"[i] DEV_MODE active — using {GROQ_MODEL_NAME} for faster iteration. "
          f"Do not use for final evaluation or demo runs.")