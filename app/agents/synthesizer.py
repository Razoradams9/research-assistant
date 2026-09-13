"""Synthesizer agent — writes the final cited report.

Single responsibility: take the aggregated ResearchBundle and produce a
coherent markdown report whose claims are backed by inline [n] citations
pointing at real sources.

Design decision — deterministic citations:
Rather than let the model invent and renumber citations (a common source
of broken references), we pre-assign a stable citation number to every
unique source URL across all findings, hand the model that fixed numbered
list, and instruct it to cite ONLY those numbers. The citations array in
the output is built from our mapping, not parsed back out of the prose,
so it can never drift from the actual sources.

If there are no findings at all (every sub-question failed or found
nothing), we short-circuit to a graceful "insufficient sources" report
instead of calling the model with nothing to work with.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from pydantic import BaseModel

from ..config import settings
from ..llm import GroqLLM
from ..schemas import (
    Citation,
    FindingsStatus,
    ResearchBundle,
    SynthesizedReport,
)
from .base import AgentError, BaseAgent

logger = logging.getLogger(__name__)

_SYSTEM = """You are the Synthesizer agent in a research pipeline.
You are given the original question, a set of sub-questions with their
findings, and a numbered list of sources.

Write a thorough, well-organized report in Markdown that answers the
original question, drawing only on the provided findings.

Rules:
- Aim for depth: a substantial multi-section report, roughly 600-900
  words. Develop each theme with 1-2 full paragraphs, not a single line.
- Explain the "why" and "how" behind facts, connect related findings,
  note nuances, tradeoffs, and any disagreements between sources.
- Support factual claims with inline citations using the exact bracketed
  numbers from the SOURCES list, e.g. "Caffeine delays sleep onset [2]."
  Weave multiple sources together where they overlap.
- Only cite numbers that appear in the SOURCES list. Never invent numbers
  or sources.
- Structure: an intro paragraph framing the topic, several themed
  sections (use ## headings) that each go into real detail, and a
  conclusion that synthesizes the takeaways. Do NOT add your own
  "References" section — that is appended automatically.
- If the findings are genuinely thin, expand on what the available
  sources do say and note the gaps, rather than writing a stub.

Return ONLY a JSON object with this exact shape:
{
  "report_markdown": "<the full markdown report with inline [n] citations>"
}
No prose outside the JSON, no markdown fences around the JSON."""

_USER_TEMPLATE = """Original question:
\"\"\"{question}\"\"\"

Findings by sub-question:
{findings_block}

SOURCES (cite by these exact numbers):
{sources_block}

Write the report as JSON now."""


class _RawReport(BaseModel):
    report_markdown: str


class SynthesizerAgent(BaseAgent):
    name = "synthesizer"

    def __init__(self, llm: Optional[GroqLLM] = None, model: Optional[str] = None) -> None:
        super().__init__(llm)
        self.model = model or settings.synthesizer_model

    async def run(self, bundle: ResearchBundle) -> SynthesizedReport:
        # 1) Build a stable url -> citation-number map from real findings.
        citations, url_to_n = _build_citations(bundle)
        covered = [
            r.sub_question_id
            for r in bundle.results
            if r.status == FindingsStatus.OK and r.findings
        ]

        # 2) No usable sources -> graceful fallback, no LLM call.
        if not citations:
            logger.warning("[synthesizer] no findings to synthesize")
            return SynthesizedReport(
                report_markdown=_empty_report(bundle),
                citations=[],
                sub_questions_covered=[],
            )

        # 3) Ask the model to write the prose with our fixed citation numbers.
        findings_block = _format_findings(bundle, url_to_n)
        sources_block = _format_sources(citations)
        user = _USER_TEMPLATE.format(
            question=bundle.original_question,
            findings_block=findings_block,
            sources_block=sources_block,
        )

        raw = await self._complete_json(
            model=self.model,
            system=_SYSTEM,
            user=user,
            schema=_RawReport,
            temperature=0.5,
            max_tokens=6144,
        )

        body = raw.report_markdown.strip()
        if not body:
            raise AgentError("[synthesizer] model returned empty report")

        # 4) Keep only citations actually referenced, then append a
        #    References section built from our trustworthy mapping.
        used_ns = _cited_numbers(body)
        used_citations = [c for c in citations if c.n in used_ns] or citations
        report_markdown = body + "\n\n" + _references_section(used_citations)

        return SynthesizedReport(
            report_markdown=report_markdown,
            citations=used_citations,
            sub_questions_covered=covered,
        )


# ── Helpers ───────────────────────────────────────────────────────────
def _build_citations(bundle: ResearchBundle) -> tuple[list[Citation], dict[str, int]]:
    """Assign a stable citation number to each unique source URL."""
    url_to_n: dict[str, int] = {}
    citations: list[Citation] = []
    n = 0
    for result in bundle.results:
        for f in result.findings:
            if not f.source_url or f.source_url in url_to_n:
                continue
            n += 1
            url_to_n[f.source_url] = n
            citations.append(
                Citation(n=n, source_url=f.source_url, source_title=f.source_title or f.source_url)
            )
    return citations, url_to_n


def _format_findings(bundle: ResearchBundle, url_to_n: dict[str, int]) -> str:
    lines: list[str] = []
    for result in bundle.results:
        header = f"### {result.sub_question_id}: {result.sub_question_text}"
        lines.append(header)
        if result.status != FindingsStatus.OK or not result.findings:
            note = {
                FindingsStatus.NO_RESULTS: "(no sources found)",
                FindingsStatus.ERROR: f"(research failed: {result.error_detail})",
            }.get(result.status, "(no findings)")
            lines.append(f"  {note}")
            continue
        for f in result.findings:
            n = url_to_n.get(f.source_url)
            tag = f"[{n}]" if n else "[?]"
            lines.append(f"  - {f.fact} {tag}")
    return "\n".join(lines)


def _format_sources(citations: list[Citation]) -> str:
    return "\n".join(f"[{c.n}] {c.source_title} — {c.source_url}" for c in citations)


def _references_section(citations: list[Citation]) -> str:
    lines = ["## References"]
    for c in sorted(citations, key=lambda x: x.n):
        lines.append(f"{c.n}. [{c.source_title}]({c.source_url})")
    return "\n".join(lines)


_CITE_RE = re.compile(r"\[(\d+)\]")


def _cited_numbers(markdown: str) -> set[int]:
    return {int(m) for m in _CITE_RE.findall(markdown)}


def _empty_report(bundle: ResearchBundle) -> str:
    reasons = []
    for r in bundle.results:
        if r.status == FindingsStatus.ERROR:
            reasons.append(f"- {r.sub_question_id}: research failed ({r.error_detail})")
        elif r.status == FindingsStatus.NO_RESULTS or not r.findings:
            reasons.append(f"- {r.sub_question_id}: no sources found")
    detail = "\n".join(reasons) if reasons else "- No sub-questions were researched."
    return (
        f"# {bundle.original_question}\n\n"
        "## Insufficient sources\n\n"
        "The research stage did not return enough usable sources to "
        "synthesize a cited report. Details:\n\n"
        f"{detail}\n\n"
        "Try rephrasing the question or running again — the web search may "
        "have been rate-limited or returned nothing relevant."
    )
