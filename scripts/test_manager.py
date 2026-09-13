"""Standalone test harness for the Manager agent.

Run it directly to see the Manager decompose sample questions into
sub-questions — no server, no other agents needed. This is the
"test it standalone on sample questions" deliverable.

Usage:
    # Use built-in sample questions:
    python -m scripts.test_manager

    # Or pass your own question:
    python -m scripts.test_manager "How does intermittent fasting affect metabolism?"

Requires GROQ_API_KEY in your environment or .env.
"""
from __future__ import annotations

import asyncio
import sys

from app.agents.manager import ManagerAgent
from app.schemas import ResearchRequest

SAMPLES = [
    "How does caffeine affect sleep quality?",
    "What are the tradeoffs of remote work for software teams?",
    "Is nuclear energy a viable path to decarbonization?",
]


async def _run_one(agent: ManagerAgent, question: str) -> None:
    print("\n" + "=" * 70)
    print(f"QUESTION: {question}")
    print("=" * 70)
    try:
        plan = await agent.run(ResearchRequest(question=question))
    except Exception as e:  # noqa: BLE001 - this is a manual test harness
        print(f"  ERROR: {type(e).__name__}: {e}")
        return

    for sq in plan.sub_questions:
        print(f"  [{sq.id}] {sq.text}")
        if sq.rationale:
            print(f"        rationale: {sq.rationale}")


async def main() -> None:
    questions = sys.argv[1:] or SAMPLES
    agent = ManagerAgent()
    for q in questions:
        await _run_one(agent, q)


if __name__ == "__main__":
    asyncio.run(main())
