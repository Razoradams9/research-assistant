"""Standalone test harness for the Researcher agent.

Runs a real web search (Tavily if TAVILY_API_KEY is set, else DuckDuckGo)
and a real Groq extraction for one or more sub-questions.

Usage:
    python -m scripts.test_researcher
    python -m scripts.test_researcher "What are the health effects of caffeine?"

Requires GROQ_API_KEY. TAVILY_API_KEY optional (falls back to DuckDuckGo).
"""
from __future__ import annotations

import asyncio
import sys

from app.agents.researcher import ResearcherAgent
from app.schemas import ResearchPlan, SubQuestion

SAMPLES = [
    "How does caffeine affect the time it takes to fall asleep?",
    "What are the documented effects of caffeine on deep sleep stages?",
]


def _progress(sqid: str, msg: str) -> None:
    print(f"    ... {msg}")


async def main() -> None:
    texts = sys.argv[1:] or SAMPLES
    plan = ResearchPlan(
        original_question="How does caffeine affect sleep?",
        sub_questions=[SubQuestion(id=f"sq{i+1}", text=t) for i, t in enumerate(texts)],
    )

    agent = ResearcherAgent()
    results = await agent.research_all(plan, on_progress=_progress)

    for r in results:
        print("\n" + "=" * 70)
        print(f"[{r.sub_question_id}] {r.sub_question_text}")
        print(f"  status={r.status.value}  provider={r.search_provider_used}")
        if r.error_detail:
            print(f"  error: {r.error_detail}")
        for f in r.findings:
            print(f"  - {f.fact}")
            print(f"      source: {f.source_title} <{f.source_url}> (conf={f.confidence})")


if __name__ == "__main__":
    asyncio.run(main())
