"""
Dual-LLM client with async rate limiting, exponential backoff, and timeout handling.

Architecture:
    llm_router.compose()  → GeminiClient  → Gemini 2.5 Flash  (message composition)
    llm_router.classify() → GroqClient    → Llama 3.3 70B     (classification tasks)

Rate limiters are module-level singletons so the RPM budget is shared across
all concurrent requests for the entire lifetime of the process.

Usage:
    from llm.client import llm_router

    body = await llm_router.compose(prompt="...", system="...")
    intent = await llm_router.classify(prompt="...")
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from typing import Optional

from google import genai
from google.genai import types as genai_types
from groq import AsyncGroq

from config import (
    CLASSIFIER_MODEL,
    CLASSIFIER_RPM_LIMIT,
    COMPOSER_MODEL,
    COMPOSER_RPM_LIMIT,
    GEMINI_API_KEY,
    GROQ_API_KEY,
    LLM_TIMEOUT_SECONDS,
    MAX_RETRIES,
    MAX_TOKENS_CLASSIFIER,
    MAX_TOKENS_COMPOSER,
)

logger = logging.getLogger("llm")

# ============================================================================
# Custom exceptions
# ============================================================================


class LLMTimeoutError(Exception):
    """Raised when an LLM call exceeds LLM_TIMEOUT_SECONDS."""


class LLMRateLimitError(Exception):
    """Raised when all retry attempts are exhausted after repeated 429s."""


# ============================================================================
# Rate limiter (token-bucket over a sliding 60-second window)
# ============================================================================


class RateLimiter:
    """Async-safe sliding-window rate limiter.

    Tracks the timestamps of the last ``rpm`` requests in a deque.  If the
    window is full and the oldest entry is less than 60 s ago, the caller
    sleeps until the slot opens.  This proactively avoids 429s rather than
    reacting to them.
    """

    def __init__(self, rpm: int) -> None:
        self._rpm = rpm
        self._window: deque[float] = deque(maxlen=rpm)
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()

            if len(self._window) >= self._rpm:
                oldest = self._window[0]
                elapsed = now - oldest
                if elapsed < 60.0:
                    wait = 60.0 - elapsed
                    logger.info(
                        "[RateLimiter] Window full (%d/%d). "
                        "Sleeping %.1fs to stay under RPM limit.",
                        len(self._window),
                        self._rpm,
                        wait,
                    )
                    await asyncio.sleep(wait)

            self._window.append(time.monotonic())


# ============================================================================
# Gemini client (message composition) — new google.genai SDK
# ============================================================================


class GeminiClient:
    """Wraps the ``google-genai`` SDK for async composition calls."""

    def __init__(self, api_key: str, model: str, rpm: int) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model_name = model
        self._rate_limiter = RateLimiter(rpm)

    async def complete(self, prompt: str, system: str = "") -> str:
        """Generate a completion with rate limiting, timeout, and retry.

        Returns the raw text response from Gemini.

        Raises:
            LLMTimeoutError:   if the call exceeds LLM_TIMEOUT_SECONDS.
            LLMRateLimitError: if all MAX_RETRIES are exhausted on 429.
        """
        await self._rate_limiter.acquire()

        last_exc: Optional[Exception] = None

        for attempt in range(MAX_RETRIES):
            try:
                result = await asyncio.wait_for(
                    self._call(prompt, system),
                    timeout=LLM_TIMEOUT_SECONDS,
                )
                return result

            except asyncio.TimeoutError:
                raise LLMTimeoutError(
                    f"Gemini call timed out after {LLM_TIMEOUT_SECONDS}s"
                )

            except Exception as exc:
                # google-genai surfaces 429 as various exception types;
                # check for "429" or "ResourceExhausted" in the message.
                exc_str = str(exc).lower()
                if "429" in exc_str or "resourceexhausted" in exc_str or "resource_exhausted" in exc_str:
                    wait = min(2 ** attempt + random.uniform(0, 1), 30)
                    logger.warning(
                        "[LLM] 429 received (Gemini). Retry %d/%d in %.1fs",
                        attempt + 1,
                        MAX_RETRIES,
                        wait,
                    )
                    last_exc = exc
                    await asyncio.sleep(wait)
                    # Re-acquire rate-limiter slot after backoff
                    await self._rate_limiter.acquire()
                else:
                    raise

        raise LLMRateLimitError(
            f"Gemini: all {MAX_RETRIES} retries exhausted on 429. "
            f"Last error: {last_exc}"
        )

    async def _call(self, prompt: str, system: str) -> str:
        """Run the blocking SDK call in a thread so we don't block the loop."""
        config = genai_types.GenerateContentConfig(
            max_output_tokens=MAX_TOKENS_COMPOSER,
            temperature=0.7,
            system_instruction=system if system else None,
        )

        # The SDK's generate_content is synchronous — run in executor
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._client.models.generate_content(
                model=self._model_name,
                contents=prompt,
                config=config,
            ),
        )
        return response.text


# ============================================================================
# Groq client (classification tasks)
# ============================================================================


class GroqClient:
    """Wraps the ``groq`` async SDK for lightweight classification calls."""

    def __init__(self, api_key: str, model: str, rpm: int) -> None:
        self._client = AsyncGroq(api_key=api_key)
        self._model = model
        self._rate_limiter = RateLimiter(rpm)

    async def complete(self, prompt: str, system: str = "") -> str:
        """Generate a completion with rate limiting, timeout, and retry.

        Returns the raw text response from Groq.

        Raises:
            LLMTimeoutError:   if the call exceeds LLM_TIMEOUT_SECONDS.
            LLMRateLimitError: if all MAX_RETRIES are exhausted on 429.
        """
        await self._rate_limiter.acquire()

        last_exc: Optional[Exception] = None

        for attempt in range(MAX_RETRIES):
            try:
                result = await asyncio.wait_for(
                    self._call(prompt, system),
                    timeout=LLM_TIMEOUT_SECONDS,
                )
                return result

            except asyncio.TimeoutError:
                raise LLMTimeoutError(
                    f"Groq call timed out after {LLM_TIMEOUT_SECONDS}s"
                )

            except Exception as exc:
                exc_str = str(exc).lower()
                if "429" in exc_str or "rate_limit" in exc_str or "rate limit" in exc_str:
                    wait = min(2 ** attempt + random.uniform(0, 1), 30)
                    logger.warning(
                        "[LLM] 429 received (Groq). Retry %d/%d in %.1fs",
                        attempt + 1,
                        MAX_RETRIES,
                        wait,
                    )
                    last_exc = exc
                    await asyncio.sleep(wait)
                    await self._rate_limiter.acquire()
                else:
                    raise

        raise LLMRateLimitError(
            f"Groq: all {MAX_RETRIES} retries exhausted on 429. "
            f"Last error: {last_exc}"
        )

    async def _call(self, prompt: str, system: str) -> str:
        """Async call to the Groq chat completions API."""
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=MAX_TOKENS_CLASSIFIER,
            temperature=0.1,
        )
        return response.choices[0].message.content


# ============================================================================
# LLM Router — the ONLY export the rest of the codebase should use
# ============================================================================


class LLMRouter:
    """Facade that exposes ``compose`` and ``classify`` on the correct backend.

    Instantiated once at module level so rate-limiter state is shared across
    the entire process lifetime.
    """

    def __init__(self) -> None:
        self._gemini = GeminiClient(
            api_key=GEMINI_API_KEY,
            model=COMPOSER_MODEL,
            rpm=COMPOSER_RPM_LIMIT,
        )
        self._groq = GroqClient(
            api_key=GROQ_API_KEY,
            model=CLASSIFIER_MODEL,
            rpm=CLASSIFIER_RPM_LIMIT,
        )
        logger.info(
            "[LLMRouter] Initialised — composer=%s (%d RPM), classifier=%s (%d RPM)",
            COMPOSER_MODEL,
            COMPOSER_RPM_LIMIT,
            CLASSIFIER_MODEL,
            CLASSIFIER_RPM_LIMIT,
        )

    async def compose(self, prompt: str, system: str = "") -> str:
        """Compose a message using Gemini 2.5 Flash."""
        return await self._gemini.complete(prompt, system)

    async def classify(self, prompt: str, system: str = "") -> str:
        """Run a lightweight classification using Groq + Llama 3.3 70B."""
        return await self._groq.complete(prompt, system)


# ---------------------------------------------------------------------------
# Module-level singleton — import this everywhere
# ---------------------------------------------------------------------------
llm_router = LLMRouter()
