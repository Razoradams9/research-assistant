"""Thin, reliable wrapper around the Groq chat API.

Responsibilities:
- Enforce a timeout on every call (your "Ollama/LLM slow" concern, now Groq).
- Retry with exponential backoff on transient errors and HTTP 429
  (Groq free tier is rate-limited per minute).
- Offer a `json_mode` that asks Groq to return strictly valid JSON.

Agents never talk to Groq directly; they go through GroqLLM so all the
reliability logic lives in one place.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from groq import (
    AsyncGroq,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)

from .config import settings

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Raised when an LLM call fails after all retries."""


class GroqLLM:
    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> None:
        key = api_key or settings.groq_api_key
        if not key:
            raise LLMError(
                "GROQ_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        self._timeout = timeout or settings.llm_timeout_seconds
        self._max_retries = max_retries if max_retries is not None else settings.llm_max_retries
        # We manage our own retries/backoff, so disable the SDK's.
        self._client = AsyncGroq(api_key=key, timeout=self._timeout, max_retries=0)

    async def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        json_mode: bool = False,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        """Run a single chat completion and return the assistant text.

        Retries on timeout, rate limit, and 5xx with exponential backoff.
        Raises LLMError if every attempt fails.
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        kwargs: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
            # gpt-oss and other Groq reasoning models emit a chain-of-thought
            # that can leak into / crowd out the content field, breaking JSON
            # parsing. "hidden" keeps reasoning out of the returned content so
            # we get clean JSON. Ignored by non-reasoning models.
            kwargs["reasoning_format"] = "hidden"

        last_err: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._client.chat.completions.create(**kwargs)
                content = resp.choices[0].message.content
                if not content:
                    raise LLMError("Groq returned empty content")
                return content
            except (APITimeoutError, RateLimitError) as e:
                last_err = e
                wait = _backoff_seconds(attempt, e)
                logger.warning(
                    "LLM call transient error (%s), attempt %d/%d, retrying in %.1fs",
                    type(e).__name__, attempt + 1, self._max_retries + 1, wait,
                )
                await asyncio.sleep(wait)
            except APIStatusError as e:
                last_err = e
                # If the model rejects reasoning_format (400), drop it and
                # retry once — keeps us compatible across Groq's model mix.
                if (
                    e.status_code == 400
                    and "reasoning_format" in kwargs
                    and "reasoning_format" in str(e).lower()
                ):
                    logger.warning("Model rejected reasoning_format; retrying without it")
                    kwargs.pop("reasoning_format", None)
                    continue
                # Retry only on server-side 5xx; other client errors are fatal.
                if 500 <= e.status_code < 600 and attempt < self._max_retries:
                    wait = _backoff_seconds(attempt, e)
                    await asyncio.sleep(wait)
                    continue
                raise LLMError(f"Groq API error {e.status_code}: {e}") from e
            except APIConnectionError as e:
                # Can't reach the API host at all. Often transient on small
                # hosts, so retry with backoff before giving up.
                last_err = e
                logger.warning(
                    "LLM connection error, attempt %d/%d: %s",
                    attempt + 1, self._max_retries + 1, e,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(_backoff_seconds(attempt, e))
                    continue
                break
            except Exception as e:  # noqa: BLE001 - surface as a clean LLMError
                last_err = e
                break

        # Include the exception TYPE so a bare "Connection error" is
        # actually diagnosable in logs/UI.
        err_type = type(last_err).__name__ if last_err else "Unknown"
        raise LLMError(
            f"LLM call failed after {self._max_retries + 1} attempts "
            f"({err_type}): {last_err}"
        )


def _backoff_seconds(attempt: int, err: Exception) -> float:
    """Exponential backoff; honor Retry-After hint if the SDK exposes one."""
    base = min(2.0 ** attempt, 8.0)
    retry_after = getattr(err, "retry_after", None)
    if isinstance(retry_after, (int, float)) and retry_after > 0:
        return float(retry_after)
    return base
