"""Researcher agent — searches the web and extracts structured findings.

Single responsibility: given one sub-question, run a web search and turn
the raw results into a list of discrete, citable Findings (fact +
source_url), keyed back to the sub-question's stable id.

Error handling is explicit and lives in the output shape:
- search returns nothing  -> status = no_results (NOT an error)
- search provider fails    -> status = error, error_detail set
- LLM extraction fails      -> status = error, error_detail set
A failure on one sub-question never aborts the others.

Concurrency: `research_all` fans out sub-questions with a bounded
semaphore (settings.max_concurrency). Since Groq does the inference
remotely, parallelism is safe here; the cap just stays polite to the
free-tier rate limit.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from pydantic import BaseModel

from ..config import settings
from ..llm import GroqLLM
from ..schemas import (
    Finding,
    FindingsStatus,
    ResearchPlan,
    SubQuestion,
    SubQuestionFindings,
)
from ..search.base import SearchError, SearchResult
from ..search.service import SearchService
from .base import AgentError, BaseAgent

logger = logging.getLogger(__name__)

# Optional callback so the pipeline can emit progress per sub-question.
ProgressCB = Callable[[str, str], None]  # (sub_question_id, message)

_SYSTEM = """You are the Researcher agent in a research pipeline.
You are given a sub-question and a numbered list of web search results
(title, url, snippet). Extract discrete, factual claims that help answer
the sub-question, each tied to the source it came from.

Rules:
- Only extract facts actually supported by the provided snippets. Do not
  invent facts or URLs.
- Use the exact source_url from the result you drew the fact from.
- Extract 4-8 substantive facts when the sources support them. Capture
  specifics: numbers, mechanisms, examples, caveats, and differing
  viewpoints — not just headline claims. Each fact should be a full,
  self-contained sentence, not a fragment.
- Draw from multiple sources rather than repeating one. Skip only truly
  irrelevant results.
- If none of the results are relevant, return an empty "findings" list.

Return ONLY a JSON object with this exact shape:
{
  "findings": [
    {
      "fact": "<a single factual claim>",
      "source_url": "<exact url from the results>",
      "source_title": "<title of that source>",
      "confidence": "high" | "medium" | "low"
    }
  ]
}
No prose, no markdown fences."""

_USER_TEMPLATE = """Sub-question:
\"\"\"{sub_question}\"\"\"

Search results:
{results_block}

Extract the supported findings as JSON."""


class _RawFinding(BaseModel):
    fact: str
    source_url: str = ""
    source_title: str = ""
    confidence: Optional[str] = None


class _RawFindings(BaseModel):
    findings: list[_RawFinding]


class ResearcherAgent(BaseAgent):
    name = "researcher"

    def __init__(
        self,
        llm: Optional[GroqLLM] = None,
        search: Optional[SearchService] = None,
        model: Optional[str] = None,
    ) -> None:
        super().__init__(llm)
        self.search = search or SearchService()
        self.model = model or settings.researcher_model

    async def research_one(
        self, sub_question: SubQuestion, on_progress: Optional[ProgressCB] = None
    ) -> SubQuestionFindings:
        """Research a single sub-question. Never raises — failures are
        captured in the returned status/error_detail."""
        sqid = sub_question.id

        # 1) Search (with Tavily -> DuckDuckGo fallback handled by service).
        try:
            outcome = await self.search.search(sub_question.text)
        except SearchError as e:
            logger.warning("[researcher] %s search failed: %s", sqid, e)
            return SubQuestionFindings(
                sub_question_id=sqid,
                sub_question_text=sub_question.text,
                status=FindingsStatus.ERROR,
                error_detail=f"search failed: {e}",
            )

        if not outcome.results:
            if on_progress:
                on_progress(sqid, f"No sources found for {sqid}")
            return SubQuestionFindings(
                sub_question_id=sqid,
                sub_question_text=sub_question.text,
                search_provider_used=outcome.provider_used,
                status=FindingsStatus.NO_RESULTS,
            )

        if on_progress:
            on_progress(
                sqid,
                f"Found {len(outcome.results)} sources for {sqid} "
                f"via {outcome.provider_used}",
            )

        # 2) Extract findings via the LLM.
        try:
            findings = await self._extract_findings(sub_question, outcome.results)
        except AgentError as e:
            logger.warning("[researcher] %s extraction failed: %s", sqid, e)
            return SubQuestionFindings(
                sub_question_id=sqid,
                sub_question_text=sub_question.text,
                search_provider_used=outcome.provider_used,
                status=FindingsStatus.ERROR,
                error_detail=f"extraction failed: {e}",
            )

        status = FindingsStatus.OK if findings else FindingsStatus.NO_RESULTS
        return SubQuestionFindings(
            sub_question_id=sqid,
            sub_question_text=sub_question.text,
            findings=findings,
            search_provider_used=outcome.provider_used,
            status=status,
        )

    async def _extract_findings(
        self, sub_question: SubQuestion, results: list[SearchResult]
    ) -> list[Finding]:
        results_block = _format_results(results)
        user = _USER_TEMPLATE.format(
            sub_question=sub_question.text, results_block=results_block
        )
        raw = await self._complete_json(
            model=self.model,
            system=_SYSTEM,
            user=user,
            schema=_RawFindings,
            temperature=0.2,
            max_tokens=2560,
        )

        # Keep only findings whose URL actually appeared in the results,
        # to defend against the model inventing sources.
        valid_urls = {r.url for r in results if r.url}
        findings: list[Finding] = []
        for rf in raw.findings:
            if not rf.fact.strip():
                continue
            url = rf.source_url.strip()
            if url and url not in valid_urls:
                # Model cited a URL we didn't give it; try to recover the
                # closest real source by title, else skip.
                match = next((r for r in results if r.title == rf.source_title), None)
                if match:
                    url = match.url
                else:
                    logger.debug("[researcher] dropping fact with unknown url: %s", url)
                    continue
            conf = rf.confidence if rf.confidence in {"high", "medium", "low"} else None
            findings.append(
                Finding(
                    fact=rf.fact.strip(),
                    source_url=url,
                    source_title=rf.source_title.strip(),
                    confidence=conf,  # type: ignore[arg-type]
                )
            )
        return findings

    async def research_all(
        self, plan: ResearchPlan, on_progress: Optional[ProgressCB] = None
    ) -> list[SubQuestionFindings]:
        """Research every sub-question with bounded concurrency."""
        sem = asyncio.Semaphore(settings.max_concurrency)

        async def _guarded(sq: SubQuestion) -> SubQuestionFindings:
            async with sem:
                return await self.research_one(sq, on_progress)

        tasks = [_guarded(sq) for sq in plan.sub_questions]
        return await asyncio.gather(*tasks)


def _format_results(results: list[SearchResult]) -> str:
    lines = []
    for i, r in enumerate(results, start=1):
        lines.append(
            f"[{i}] title: {r.title}\n    url: {r.url}\n    snippet: {r.content}"
        )
    return "\n".join(lines)
