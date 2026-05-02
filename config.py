"""
Central configuration for the Vera bot.

Loads API keys from environment variables and defines all tuning constants.
Fails fast at import time if required keys are missing.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# API Keys — loaded from .env / environment
# ---------------------------------------------------------------------------

GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")

_missing: list[str] = []
if not GEMINI_API_KEY:
    _missing.append("GEMINI_API_KEY")
if not GROQ_API_KEY:
    _missing.append("GROQ_API_KEY")

if _missing:
    raise EnvironmentError(
        f"Missing required environment variable(s): {', '.join(_missing)}. "
        f"Create a .env file (see .env.example) or export them in your shell."
    )

# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------

COMPOSER_MODEL: str = "gemini-2.5-flash"          # Gemini — message composition
CLASSIFIER_MODEL: str = "llama-3.3-70b-versatile"  # Groq  — classification tasks

# ---------------------------------------------------------------------------
# Rate limits (free-tier ceilings)
# ---------------------------------------------------------------------------

COMPOSER_RPM_LIMIT: int = 15   # Gemini free tier: 15 requests/minute
CLASSIFIER_RPM_LIMIT: int = 30  # Groq free tier:  30 requests/minute

# ---------------------------------------------------------------------------
# Timeouts & retries
# ---------------------------------------------------------------------------

LLM_TIMEOUT_SECONDS: int = 25  # Hard ceiling — judge timeout is 30s
MAX_RETRIES: int = 4           # Retry attempts on 429 before giving up

# ---------------------------------------------------------------------------
# Token budgets
# ---------------------------------------------------------------------------

MAX_TOKENS_COMPOSER: int = 500   # Enough for a WhatsApp message + rationale
MAX_TOKENS_CLASSIFIER: int = 150  # Short classification output
