"""Offline smoke test — no API key or network needed.

Validates the pieces that don't require Groq:
- JSON extraction from messy model output (fenced, prose-wrapped, bare)
- Schema defaults and validation
- Stable-id assignment logic mirrors what ManagerAgent does

Run: python -m scripts.smoke_test
"""
from __future__ import annotations

import asyncio

from app.agents.base import _extract_json
from app.schemas import ProgressEvent, ResearchRequest, SubQuestion
from app.search.base import SearchError, SearchResult
from app.search.service import SearchService


def check(label: str, cond: bool) -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        raise SystemExit(f"Smoke test failed: {label}")


def main() -> None:
    print("JSON extraction:")
    fenced = "```json\n{\"a\": 1}\n```"
    check("fenced block", _extract_json(fenced) == {"a": 1})

    prose = 'Here is your answer: {"x": 1, "y": 2} hope that helps!'
    check("prose-wrapped object", _extract_json(prose) == {"x": 1, "y": 2})

    bare = '{"ok": true}'
    check("bare object", _extract_json(bare) == {"ok": True})

    print("Schema behavior:")
    req = ResearchRequest(question="test question")
    check("ResearchRequest default cap", req.max_sub_questions == 4)

    evt = ProgressEvent(run_id="r1", stage="manager", status="started", message="planning")
    check("ProgressEvent auto timestamp", bool(evt.timestamp))

    sq = SubQuestion(id="sq1", text="What is X?")
    check("SubQuestion default rationale", sq.rationale == "")

    print("Search fallback logic:")
    asyncio.run(_check_search_fallback())

    print("Researcher error handling:")
    asyncio.run(_check_researcher())

    print("Synthesizer citation logic:")
    asyncio.run(_check_synthesizer())

    print("SQLite store round-trip:")
    asyncio.run(_check_store())

    print("Pipeline orchestration (fake agents):")
    asyncio.run(_check_pipeline())

    print("\nAll smoke checks passed.")


# ── Fake providers to exercise SearchService branching offline ────────
class _FakeProvider:
    def __init__(self, name, results=None, raises=False, available=True):
        self.name = name
        self._results = results or []
        self._raises = raises
        self.available = available
        self.calls = 0

    async def search(self, query, *, max_results):
        self.calls += 1
        if self._raises:
            raise SearchError(f"{self.name} boom")
        return list(self._results)


def _res(url):
    return SearchResult(title=f"t-{url}", url=url, content="snippet")


async def _check_search_fallback() -> None:
    # 1) Tavily available + returns results -> use tavily.
    tav = _FakeProvider("tavily", results=[_res("http://a")])
    ddg = _FakeProvider("duckduckgo", results=[_res("http://b")])
    svc = SearchService(tavily=tav, duckduckgo=ddg)
    out = await svc.search("q")
    check("primary used when it returns results", out.provider_used == "tavily" and ddg.calls == 0)

    # 2) Tavily returns empty -> fall back to DDG.
    tav = _FakeProvider("tavily", results=[])
    ddg = _FakeProvider("duckduckgo", results=[_res("http://b")])
    svc = SearchService(tavily=tav, duckduckgo=ddg)
    out = await svc.search("q")
    check("fallback on empty primary", out.provider_used == "duckduckgo" and ddg.calls == 1)

    # 3) Tavily raises -> fall back to DDG.
    tav = _FakeProvider("tavily", raises=True)
    ddg = _FakeProvider("duckduckgo", results=[_res("http://b")])
    svc = SearchService(tavily=tav, duckduckgo=ddg)
    out = await svc.search("q")
    check("fallback on primary error", out.provider_used == "duckduckgo")

    # 4) Tavily not configured -> skip straight to DDG.
    tav = _FakeProvider("tavily", available=False)
    ddg = _FakeProvider("duckduckgo", results=[_res("http://b")])
    svc = SearchService(tavily=tav, duckduckgo=ddg)
    out = await svc.search("q")
    check("skip unconfigured primary", out.provider_used == "duckduckgo" and tav.calls == 0)

    # 5) Both empty -> no_results (empty list, no raise).
    tav = _FakeProvider("tavily", results=[])
    ddg = _FakeProvider("duckduckgo", results=[])
    svc = SearchService(tavily=tav, duckduckgo=ddg)
    out = await svc.search("q")
    check("both empty -> empty results, provider=ddg", out.results == [] and out.provider_used == "duckduckgo")


class _FakeSearchService:
    """Stands in for SearchService in researcher tests."""

    def __init__(self, outcome=None, raises=False):
        self._outcome = outcome
        self._raises = raises

    async def search(self, query, *, max_results=None):
        if self._raises:
            raise SearchError("search down")
        return self._outcome


class _FakeLLM:
    """Returns a canned JSON string; stands in for GroqLLM."""

    def __init__(self, payload):
        self._payload = payload

    async def complete(self, **kwargs):
        return self._payload


async def _check_researcher() -> None:
    from app.agents.researcher import ResearcherAgent
    from app.schemas import FindingsStatus, SubQuestion
    from app.search.service import SearchOutcome

    sq = SubQuestion(id="sq1", text="What is X?")

    # a) search raises -> status error, does not throw.
    agent = ResearcherAgent(llm=_FakeLLM("{}"), search=_FakeSearchService(raises=True))
    r = await agent.research_one(sq)
    check("search failure -> status error", r.status == FindingsStatus.ERROR and r.error_detail)

    # b) no results -> status no_results.
    agent = ResearcherAgent(
        llm=_FakeLLM("{}"),
        search=_FakeSearchService(SearchOutcome(results=[], provider_used="duckduckgo")),
    )
    r = await agent.research_one(sq)
    check("empty search -> status no_results", r.status == FindingsStatus.NO_RESULTS)

    # c) results + valid LLM findings -> status ok, invented URL dropped.
    outcome = SearchOutcome(results=[_res("http://real")], provider_used="tavily")
    payload = (
        '{"findings": ['
        '{"fact": "real fact", "source_url": "http://real", "source_title": "t-http://real", "confidence": "high"},'
        '{"fact": "fake fact", "source_url": "http://invented", "source_title": "nope", "confidence": "low"}'
        "]}"
    )
    agent = ResearcherAgent(llm=_FakeLLM(payload), search=_FakeSearchService(outcome))
    r = await agent.research_one(sq)
    kept = [f.source_url for f in r.findings]
    check(
        "valid findings kept, invented url dropped",
        r.status == FindingsStatus.OK and kept == ["http://real"],
    )


async def _check_synthesizer() -> None:
    from app.agents.synthesizer import (
        SynthesizerAgent,
        _build_citations,
        _cited_numbers,
    )
    from app.schemas import (
        Finding,
        FindingsStatus,
        ResearchBundle,
        SubQuestionFindings,
    )

    bundle = ResearchBundle(
        original_question="Q?",
        results=[
            SubQuestionFindings(
                sub_question_id="sq1",
                sub_question_text="a",
                status=FindingsStatus.OK,
                findings=[
                    Finding(fact="f1", source_url="http://a", source_title="A"),
                    Finding(fact="f2", source_url="http://a", source_title="A"),  # dup url
                    Finding(fact="f3", source_url="http://b", source_title="B"),
                ],
            ),
            SubQuestionFindings(
                sub_question_id="sq2", sub_question_text="b", status=FindingsStatus.NO_RESULTS
            ),
        ],
    )

    citations, url_to_n = _build_citations(bundle)
    check("dedupes urls into stable numbers", url_to_n == {"http://a": 1, "http://b": 2})
    check("citations count matches unique urls", len(citations) == 2)

    check("cited-number extraction", _cited_numbers("x [1] y [2] z [1]") == {1, 2})

    # Empty bundle -> graceful fallback report, no LLM call, no citations.
    empty = ResearchBundle(
        original_question="Nothing found?",
        results=[
            SubQuestionFindings(
                sub_question_id="sq1", sub_question_text="a", status=FindingsStatus.NO_RESULTS
            )
        ],
    )
    agent = SynthesizerAgent(llm=_FakeLLM("{}"))
    report = await agent.run(empty)
    check(
        "empty bundle -> insufficient-sources fallback",
        "Insufficient sources" in report.report_markdown and report.citations == [],
    )

    # Full path with a fake LLM that cites [1] and [2]; References appended.
    payload = '{"report_markdown": "Intro. Fact one [1]. Fact three [2]."}'
    agent = SynthesizerAgent(llm=_FakeLLM(payload))
    report = await agent.run(bundle)
    check("report appends References section", "## References" in report.report_markdown)
    check("covered lists only OK sub-questions", report.sub_questions_covered == ["sq1"])
    check("citations preserved", {c.n for c in report.citations} == {1, 2})


async def _check_store() -> None:
    import tempfile
    from pathlib import Path

    from app.schemas import (
        Finding,
        FindingsStatus,
        ResearchBundle,
        ResearchPlan,
        SubQuestion,
        SubQuestionFindings,
        SynthesizedReport,
        Citation,
    )
    from app.store import RunStore

    tmpdir = tempfile.mkdtemp()
    store = RunStore(db_path=str(Path(tmpdir) / "test.db"))

    plan = ResearchPlan(
        original_question="Q?", sub_questions=[SubQuestion(id="sq1", text="a")]
    )
    bundle = ResearchBundle(
        original_question="Q?",
        results=[
            SubQuestionFindings(
                sub_question_id="sq1",
                sub_question_text="a",
                status=FindingsStatus.OK,
                findings=[Finding(fact="f", source_url="http://a", source_title="A")],
            )
        ],
    )
    report = SynthesizedReport(
        report_markdown="body [1]",
        citations=[Citation(n=1, source_url="http://a", source_title="A")],
        sub_questions_covered=["sq1"],
    )

    await store.save(
        run_id="run_x", question="Q?", status="completed",
        plan=plan, bundle=bundle, report=report,
    )
    got = await store.get("run_x")
    check("store persists and reloads run", got is not None and got["status"] == "completed")
    check("stored plan deserializes", got["plan"]["sub_questions"][0]["id"] == "sq1")
    check("stored report deserializes", got["report"]["citations"][0]["source_url"] == "http://a")

    recent = await store.list_recent()
    check("list_recent returns the run", any(r["run_id"] == "run_x" for r in recent))

    missing = await store.get("nope")
    check("missing run returns None", missing is None)


# ── Fake agents to exercise the pipeline end-to-end offline ───────────
class _FakeManager:
    async def run(self, request):
        from app.schemas import ResearchPlan, SubQuestion
        return ResearchPlan(
            original_question=request.question,
            sub_questions=[SubQuestion(id="sq1", text="sub one", rationale="r")],
        )


class _FakeResearcher:
    async def research_all(self, plan, on_progress=None):
        from app.schemas import Finding, FindingsStatus, SubQuestionFindings
        if on_progress:
            on_progress("sq1", "Found 2 sources for sq1 via duckduckgo")
        return [
            SubQuestionFindings(
                sub_question_id="sq1",
                sub_question_text="sub one",
                status=FindingsStatus.OK,
                search_provider_used="duckduckgo",
                findings=[Finding(fact="a fact", source_url="http://a", source_title="A")],
            )
        ]


class _FakeSynth:
    async def run(self, bundle):
        from app.schemas import Citation, SynthesizedReport
        return SynthesizedReport(
            report_markdown="Report body [1]\n\n## References\n1. [A](http://a)",
            citations=[Citation(n=1, source_url="http://a", source_title="A")],
            sub_questions_covered=["sq1"],
        )


async def _check_pipeline() -> None:
    import tempfile
    from pathlib import Path

    from app.pipeline import Pipeline
    from app.schemas import ResearchRequest
    from app.store import RunStore

    tmpdir = tempfile.mkdtemp()
    store = RunStore(db_path=str(Path(tmpdir) / "pipe.db"))
    pipe = Pipeline(
        store=store,
        manager=_FakeManager(),
        researcher=_FakeResearcher(),
        synthesizer=_FakeSynth(),
    )

    events = []

    async def emit(ev):
        events.append(ev)

    report = await pipe.run("run_p", ResearchRequest(question="Test?"), emit)

    stages = [e.stage for e in events]
    check("emits manager stage", "manager" in stages)
    check("emits researcher stage", "researcher" in stages)
    check("emits synthesizer stage", "synthesizer" in stages)
    check("emits done stage last", stages[-1] == "done")
    check("returns a report", report is not None and "Report body" in report.report_markdown)
    check("forwards per-subquestion progress",
          any("Found 2 sources for sq1" in e.message for e in events))

    saved = await store.get("run_p")
    check("pipeline persists completed run", saved is not None and saved["status"] == "completed")

    # Hard failure in Manager -> error event + persisted 'failed' run, no crash.
    pipe2 = Pipeline(
        store=store,
        manager=_BoomManager(),
        researcher=_FakeResearcher(),
        synthesizer=_FakeSynth(),
    )
    events2 = []

    async def emit2(ev):
        events2.append(ev)

    report2 = await pipe2.run("run_f", ResearchRequest(question="Boom?"), emit2)
    check("hard failure returns None", report2 is None)
    check("hard failure emits error event", any(e.stage == "error" for e in events2))
    failed = await store.get("run_f")
    check("failed run persisted as failed", failed is not None and failed["status"] == "failed")


class _BoomManager:
    async def run(self, request):
        from app.agents.base import AgentError
        raise AgentError("manager exploded")


if __name__ == "__main__":
    main()
