"""The typed handoff contract between agent stages.

This is the single source of truth for the shape of data as it moves
through the pipeline:

    ResearchRequest
        -> ManagerAgent   -> ResearchPlan
        -> ResearcherAgent -> SubQuestionFindings[]  (bundled as ResearchBundle)
        -> SynthesizerAgent -> SynthesizedReport
        -> CriticAgent    -> CritiqueResult (stretch)

Every agent validates its input and output against these models, so a
malformed handoff is caught at the boundary instead of corrupting a
later stage. ProgressEvent is the payload streamed to the frontend over
SSE to power the live reasoning trail.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Stage 0: user input ───────────────────────────────────────────────
class ResearchRequest(BaseModel):
    question: str = Field(..., min_length=3, description="Raw user research question")
    max_sub_questions: int = Field(
        default=5, ge=1, le=8, description="Guardrail on how many sub-questions to plan"
    )


# ── Stage 1: Manager output -> Researcher input ───────────────────────
class SubQuestion(BaseModel):
    id: str = Field(..., description="Stable handle, e.g. 'sq1' — the spine of the system")
    text: str = Field(..., description="The focused sub-question")
    rationale: str = Field(default="", description="Why this sub-question matters")


class ResearchPlan(BaseModel):
    original_question: str
    sub_questions: list[SubQuestion]


# ── Stage 2: Researcher output ────────────────────────────────────────
Confidence = Literal["high", "medium", "low"]


class Finding(BaseModel):
    fact: str = Field(..., description="A discrete, citable claim")
    source_url: str = Field(..., description="URL backing the fact")
    source_title: str = Field(default="", description="Human-readable source name")
    confidence: Optional[Confidence] = Field(
        default=None, description="Optional; local/small models may not rate reliably"
    )


class FindingsStatus(str, Enum):
    OK = "ok"
    NO_RESULTS = "no_results"
    ERROR = "error"


class SubQuestionFindings(BaseModel):
    sub_question_id: str
    sub_question_text: str
    findings: list[Finding] = Field(default_factory=list)
    search_provider_used: Optional[Literal["tavily", "duckduckgo"]] = None
    status: FindingsStatus = FindingsStatus.OK
    error_detail: Optional[str] = None


class ResearchBundle(BaseModel):
    original_question: str
    results: list[SubQuestionFindings] = Field(default_factory=list)


# ── Stage 3: Synthesizer output ───────────────────────────────────────
class Citation(BaseModel):
    n: int = Field(..., description="Citation number referenced inline as [n]")
    source_url: str
    source_title: str = ""


class SynthesizedReport(BaseModel):
    report_markdown: str
    citations: list[Citation] = Field(default_factory=list)
    sub_questions_covered: list[str] = Field(default_factory=list)


# ── Stage 4: Critic output (stretch) ──────────────────────────────────
class Issue(BaseModel):
    type: Literal["gap", "contradiction", "weak_sourcing", "other"]
    description: str
    related_sub_question_id: Optional[str] = None


class CritiqueResult(BaseModel):
    verdict: Literal["approved", "revise"]
    issues: list[Issue] = Field(default_factory=list)
    revision_instructions: Optional[str] = None


# ── Progress events (SSE payload -> frontend) ─────────────────────────
Stage = Literal["manager", "researcher", "synthesizer", "critic", "done", "error"]
EventStatus = Literal["started", "in_progress", "completed", "failed"]


class ProgressEvent(BaseModel):
    run_id: str
    stage: Stage
    status: EventStatus
    message: str
    data: Optional[dict[str, Any]] = None
    timestamp: str = Field(default_factory=_utc_now_iso)
