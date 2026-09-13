"""Manager agent — the pipeline's planner.

Single responsibility: take the user's research question and break it
into 3-5 focused, non-overlapping sub-questions that together cover the
topic. Output is a validated ResearchPlan with stable ids (sq1, sq2...).

Those ids are the spine of the whole system: the Researcher keys its
findings to them, the Synthesizer traces citations through them, and
progress events name them.
"""
from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel

from ..config import settings
from ..llm import GroqLLM
from ..schemas import ResearchPlan, ResearchRequest, SubQuestion
from .base import AgentError, BaseAgent

logger = logging.getLogger(__name__)


class _RawSubQ(BaseModel):
    """Loose shape the LLM returns; we assign stable ids ourselves."""

    text: str
    rationale: str = ""


class _RawPlan(BaseModel):
    sub_questions: list[_RawSubQ]

_SYSTEM = """You are the Manager agent in a research pipeline.
Your job: decompose a user's research question into focused sub-questions.

Rules:
- Produce between 3 and {max_sq} sub-questions.
- Each must be focused, answerable by web research, and non-overlapping.
- Together they should cover the main facets of the original question.
- Give each a short rationale explaining why it matters.

Return ONLY a JSON object with this exact shape:
{{
  "sub_questions": [
    {{"text": "<focused sub-question>", "rationale": "<why it matters>"}}
  ]
}}
No prose, no markdown fences."""

_USER = """Original research question:
\"\"\"{question}\"\"\"

Break it into 3 to {max_sq} focused sub-questions as JSON."""


class ManagerAgent(BaseAgent):
    name = "manager"

    def __init__(self, llm: Optional[GroqLLM] = None, model: Optional[str] = None) -> None:
        super().__init__(llm)
        self.model = model or settings.manager_model

    async def run(self, request: ResearchRequest) -> ResearchPlan:
        max_sq = min(request.max_sub_questions, settings.max_sub_questions)
        system = _SYSTEM.format(max_sq=max_sq)
        user = _USER.format(question=request.question, max_sq=max_sq)

        # Validate against a loose shape first, then assign stable ids.
        raw = await self._complete_json(
            model=self.model,
            system=system,
            user=user,
            schema=_RawPlan,
            temperature=0.4,
            max_tokens=1024,
        )

        if not raw.sub_questions:
            raise AgentError("[manager] Model returned zero sub-questions")

        # Trim to the cap and assign stable ids.
        trimmed = raw.sub_questions[:max_sq]
        sub_questions = [
            SubQuestion(id=f"sq{i + 1}", text=sq.text.strip(), rationale=sq.rationale.strip())
            for i, sq in enumerate(trimmed)
        ]

        plan = ResearchPlan(
            original_question=request.question,
            sub_questions=sub_questions,
        )
        logger.info("[manager] planned %d sub-questions", len(sub_questions))
        return plan
