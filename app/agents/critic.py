"""Critic agent (stretch) — reviews the synthesized report.

Single responsibility: read the report plus the underlying findings and
decide whether it's good enough ("approved") or needs another synthesis
pass ("revise"), returning specific, structured feedback.

It is fully implemented but gated behind settings.enable_critic (off by
default) so the core 3-agent flow stands on its own. When enabled, the
pipeline runs synthesize -> critique -> (re-synthesize with feedback),
capped at settings.max_critic_revisions passes.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..config import settings
from ..llm import GroqLLM
from ..schemas import CritiqueResult, ResearchBundle, SynthesizedReport
from .base import BaseAgent

logger = logging.getLogger(__name__)

_SYSTEM = """You are the Critic agent in a research pipeline.
You review a synthesized report against the findings it was built from
and judge its quality.

Check for:
- gaps: sub-questions or important aspects left unaddressed.
- contradiction: claims that conflict with each other or the findings.
- weak_sourcing: claims resting on too few or low-quality sources.

Be fair: approve a report that is coherent and reasonably sourced. Only
ask for revision when there is a concrete, fixable problem.

Return ONLY a JSON object with this exact shape:
{
  "verdict": "approved" | "revise",
  "issues": [
    {
      "type": "gap" | "contradiction" | "weak_sourcing" | "other",
      "description": "<specific problem>",
      "related_sub_question_id": "<sqN or null>"
    }
  ],
  "revision_instructions": "<concrete guidance for the next pass, or null>"
}
No prose outside the JSON, no markdown fences."""

_USER_TEMPLATE = """Original question:
\"\"\"{question}\"\"\"

Findings that were available:
{findings_block}

The report under review:
\"\"\"
{report}
\"\"\"

Critique it as JSON now."""


class CriticAgent(BaseAgent):
    name = "critic"

    def __init__(self, llm: Optional[GroqLLM] = None, model: Optional[str] = None) -> None:
        super().__init__(llm)
        self.model = model or settings.critic_model

    async def run(
        self, report: SynthesizedReport, bundle: ResearchBundle
    ) -> CritiqueResult:
        findings_block = _summarize_findings(bundle)
        user = _USER_TEMPLATE.format(
            question=bundle.original_question,
            findings_block=findings_block,
            report=report.report_markdown,
        )
        result = await self._complete_json(
            model=self.model,
            system=_SYSTEM,
            user=user,
            schema=CritiqueResult,
            temperature=0.3,
            max_tokens=1024,
        )
        logger.info("[critic] verdict=%s issues=%d", result.verdict, len(result.issues))
        return result


def _summarize_findings(bundle: ResearchBundle) -> str:
    lines: list[str] = []
    for r in bundle.results:
        lines.append(f"- [{r.sub_question_id}] {r.sub_question_text} (status={r.status.value})")
        for f in r.findings:
            lines.append(f"    * {f.fact} <{f.source_url}>")
    return "\n".join(lines)
