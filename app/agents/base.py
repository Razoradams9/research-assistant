"""Shared base for all agents.

Each agent is a class with a single responsibility and a `run` method.
The base provides the two things every agent needs:

1. A handle to the LLM wrapper.
2. `parse_json_response` — robust JSON extraction + Pydantic validation
   with a bounded "reminder" retry. This is where we defend against the
   #1 failure mode of local/small models: output that isn't clean JSON.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from ..llm import GroqLLM, LLMError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class AgentError(RuntimeError):
    """Raised when an agent cannot produce valid output."""


class BaseAgent:
    #: Human-readable name used in logs and progress messages.
    name: str = "agent"

    def __init__(self, llm: Optional[GroqLLM] = None) -> None:
        # Lazy: don't construct GroqLLM (which requires an API key) until a
        # call is actually made. This keeps agents importable/constructible
        # in tests and at app startup before a key is configured.
        self._llm = llm

    @property
    def llm(self) -> GroqLLM:
        if self._llm is None:
            self._llm = GroqLLM()
        return self._llm

    @llm.setter
    def llm(self, value: GroqLLM) -> None:
        self._llm = value

    async def _complete_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: Type[T],
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> T:
        """Call the LLM in JSON mode and validate against `schema`.

        Retries once with an explicit "return valid JSON only" reminder if
        the first response fails to parse or validate.
        """
        prompt = user
        last_err: Optional[Exception] = None

        for attempt in range(2):  # original + one reminder retry
            try:
                raw = await self.llm.complete(
                    model=model,
                    system=system,
                    user=prompt,
                    json_mode=True,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except LLMError as e:
                raise AgentError(f"[{self.name}] LLM call failed: {e}") from e

            try:
                data = _extract_json(raw)
                return schema.model_validate(data)
            except (json.JSONDecodeError, ValidationError, ValueError) as e:
                last_err = e
                logger.warning(
                    "[%s] JSON parse/validation failed (attempt %d): %s",
                    self.name, attempt + 1, e,
                )
                prompt = (
                    f"{user}\n\n"
                    "Your previous response was not valid JSON matching the required "
                    f"schema. Error: {e}. Respond with ONLY the JSON object, no prose, "
                    "no markdown fences."
                )

        raise AgentError(
            f"[{self.name}] Could not obtain valid JSON after retry: {last_err}"
        )


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json(text: str) -> dict:
    """Best-effort extraction of a JSON object from model output.

    Handles the common cases where a model wraps JSON in markdown fences
    or adds a stray sentence before/after the object.
    """
    text = text.strip()
    # 1) Try a fenced block first.
    m = _FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    # 2) Direct parse.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 3) Fall back to the outermost {...} span.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("No JSON object found in model output")
