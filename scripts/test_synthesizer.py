"""Standalone test harness for the Synthesizer agent.

Feeds a hand-built ResearchBundle (canned findings, no web search needed)
into the Synthesizer and prints the resulting cited markdown report.
This lets you eyeball citation quality and prose without running the
whole pipeline.

Usage:
    python -m scripts.test_synthesizer

Requires GROQ_API_KEY.
"""
from __future__ import annotations

import asyncio

from app.agents.synthesizer import SynthesizerAgent
from app.schemas import (
    Finding,
    FindingsStatus,
    ResearchBundle,
    SubQuestionFindings,
)

BUNDLE = ResearchBundle(
    original_question="How does caffeine affect sleep quality?",
    results=[
        SubQuestionFindings(
            sub_question_id="sq1",
            sub_question_text="How does caffeine affect time to fall asleep?",
            search_provider_used="duckduckgo",
            status=FindingsStatus.OK,
            findings=[
                Finding(
                    fact="Caffeine consumed within 6 hours of bedtime can significantly "
                    "lengthen the time it takes to fall asleep.",
                    source_url="https://example.com/caffeine-onset",
                    source_title="Sleep Foundation",
                    confidence="high",
                ),
            ],
        ),
        SubQuestionFindings(
            sub_question_id="sq2",
            sub_question_text="How does caffeine affect deep sleep stages?",
            search_provider_used="duckduckgo",
            status=FindingsStatus.OK,
            findings=[
                Finding(
                    fact="Caffeine reduces slow-wave (deep) sleep, lowering overall "
                    "sleep quality even when total sleep time is unchanged.",
                    source_url="https://example.com/deep-sleep",
                    source_title="Journal of Sleep Research",
                    confidence="medium",
                ),
            ],
        ),
        SubQuestionFindings(
            sub_question_id="sq3",
            sub_question_text="Does caffeine tolerance reduce its sleep effects?",
            status=FindingsStatus.NO_RESULTS,
        ),
    ],
)


async def main() -> None:
    agent = SynthesizerAgent()
    report = await agent.run(BUNDLE)
    print("=" * 70)
    print("REPORT MARKDOWN")
    print("=" * 70)
    print(report.report_markdown)
    print("\n" + "=" * 70)
    print(f"CITATIONS ({len(report.citations)}):")
    for c in report.citations:
        print(f"  [{c.n}] {c.source_title} -> {c.source_url}")
    print(f"SUB-QUESTIONS COVERED: {report.sub_questions_covered}")


if __name__ == "__main__":
    asyncio.run(main())
