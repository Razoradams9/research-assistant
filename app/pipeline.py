"""Orchestration: chains Manager -> Researcher -> Synthesizer (-> Critic).

The pipeline emits ProgressEvent objects at every meaningful step via an
async `emit` callback. The FastAPI layer wires that callback to an
asyncio.Queue which is drained to the browser over SSE — that's what
powers the live reasoning trail.

Failure policy:
- A failed sub-question does NOT abort the run (handled inside the
  Researcher). The Synthesizer degrades gracefully if findings are thin.
- A hard failure in Manager or Synthesizer emits an 'error' event, marks
  the run failed, persists it, and stops.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

from .agents.critic import CriticAgent
from .agents.manager import ManagerAgent
from .agents.researcher import ResearcherAgent
from .agents.synthesizer import SynthesizerAgent
from .agents.base import AgentError
from .config import settings
from .llm import LLMError
from .schemas import (
    CritiqueResult,
    ProgressEvent,
    ResearchBundle,
    ResearchPlan,
    ResearchRequest,
    SynthesizedReport,
)
from .store import RunStore

logger = logging.getLogger(__name__)

# Async callback the pipeline uses to publish a progress event.
EmitFn = Callable[[ProgressEvent], Awaitable[None]]


class Pipeline:
    def __init__(
        self,
        store: Optional[RunStore] = None,
        manager: Optional[ManagerAgent] = None,
        researcher: Optional[ResearcherAgent] = None,
        synthesizer: Optional[SynthesizerAgent] = None,
        critic: Optional[CriticAgent] = None,
    ) -> None:
        self.store = store or RunStore()
        self.manager = manager or ManagerAgent()
        self.researcher = researcher or ResearcherAgent()
        self.synthesizer = synthesizer or SynthesizerAgent()
        self.critic = critic or CriticAgent()

    async def run(
        self, run_id: str, request: ResearchRequest, emit: EmitFn
    ) -> Optional[SynthesizedReport]:
        async def event(stage, status, message, data=None):
            await emit(
                ProgressEvent(
                    run_id=run_id, stage=stage, status=status, message=message, data=data
                )
            )

        plan: Optional[ResearchPlan] = None
        bundle: Optional[ResearchBundle] = None

        try:
            # ── Stage 1: Manager ──────────────────────────────────────
            await event("manager", "started", "Manager is planning sub-questions...")
            plan = await self.manager.run(request)
            await event(
                "manager",
                "completed",
                f"Planned {len(plan.sub_questions)} sub-questions.",
                data={"sub_questions": [sq.model_dump() for sq in plan.sub_questions]},
            )

            # ── Stage 2: Researcher (parallel, bounded) ───────────────
            await event(
                "researcher",
                "started",
                f"Researching {len(plan.sub_questions)} sub-questions...",
            )

            # Bridge the researcher's sync progress callback to async events.
            progress_msgs: list[tuple[str, str]] = []

            def on_progress(sqid: str, msg: str) -> None:
                progress_msgs.append((sqid, msg))

            results = await self.researcher.research_all(plan, on_progress=on_progress)
            for sqid, msg in progress_msgs:
                await event("researcher", "in_progress", msg, data={"sub_question_id": sqid})

            bundle = ResearchBundle(
                original_question=request.question, results=results
            )
            ok = sum(1 for r in results if r.status.value == "ok" and r.findings)
            await event(
                "researcher",
                "completed",
                f"Research done: {ok}/{len(results)} sub-questions yielded findings.",
                data={"results": [r.model_dump() for r in results]},
            )

            # ── Stage 3: Synthesizer (+ optional Critic loop) ─────────
            report = await self._synthesize_with_optional_critic(bundle, event)

            await event(
                "done",
                "completed",
                "Report ready.",
                data={"report": report.model_dump()},
            )
            await self.store.save(
                run_id=run_id,
                question=request.question,
                status="completed",
                plan=plan,
                bundle=bundle,
                report=report,
            )
            return report

        except (AgentError, LLMError) as e:
            logger.exception("[pipeline] run %s failed", run_id)
            await event("error", "failed", f"Pipeline failed: {e}")
            await self.store.save(
                run_id=run_id,
                question=request.question,
                status="failed",
                plan=plan,
                bundle=bundle,
                error=str(e),
            )
            return None

    async def _synthesize_with_optional_critic(
        self, bundle: ResearchBundle, event
    ) -> SynthesizedReport:
        await event("synthesizer", "started", "Synthesizing report...")
        report = await self.synthesizer.run(bundle)
        await event("synthesizer", "completed", "Draft report written.")

        if not settings.enable_critic:
            return report

        for attempt in range(settings.max_critic_revisions):
            await event("critic", "started", "Critic is reviewing the report...")
            critique: CritiqueResult = await self.critic.run(report, bundle)

            if critique.verdict == "approved":
                await event("critic", "completed", "Critic approved the report.")
                return report

            issues = "; ".join(i.description for i in critique.issues) or "unspecified issues"
            await event(
                "critic",
                "in_progress",
                f"Critic requested a revision (pass {attempt + 1}): {issues}",
                data={"critique": critique.model_dump()},
            )

            # Re-synthesize with the critique folded into the bundle context.
            await event("synthesizer", "in_progress", "Revising report from critique...")
            report = await self.synthesizer.run(
                _bundle_with_feedback(bundle, critique)
            )
            await event("synthesizer", "completed", "Revised report written.")

        await event("critic", "completed", "Max revisions reached; returning latest report.")
        return report


def _bundle_with_feedback(bundle: ResearchBundle, critique: CritiqueResult) -> ResearchBundle:
    """Attach critic guidance to the question so the synthesizer sees it.

    We keep findings identical and only enrich the question text with the
    revision instructions — a light-touch way to steer the next pass
    without changing the handoff contract.
    """
    instr = critique.revision_instructions or "; ".join(
        i.description for i in critique.issues
    )
    enriched_q = (
        f"{bundle.original_question}\n\n"
        f"[Revision guidance from critic: {instr}]"
    )
    return ResearchBundle(original_question=enriched_q, results=bundle.results)
