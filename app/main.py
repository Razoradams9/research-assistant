"""FastAPI application: start runs, stream live progress over SSE, serve UI.

Endpoints:
  POST /api/research                 -> { run_id }   (kicks off pipeline in background)
  GET  /api/research/{run_id}/stream -> text/event-stream of ProgressEvent JSON
  GET  /api/runs                     -> recent persisted runs
  GET  /api/runs/{run_id}            -> a single persisted run (plan/bundle/report)
  GET  /                             -> the live-progress frontend

The POST/stream split lets the browser open the SSE connection and *then*
watch the whole pipeline unfold from the first event.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .pipeline import Pipeline
from .runmanager import run_manager
from .schemas import ResearchRequest
from .store import RunStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Multi-Agent Research Assistant")

store = RunStore()
pipeline = Pipeline(store=store)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@app.post("/api/research")
async def start_research(request: ResearchRequest):
    """Start a pipeline run in the background; return its run_id."""
    run_id = run_manager.new_run_id()
    run_manager.register(run_id)

    async def _runner():
        try:
            await pipeline.run(run_id, request, run_manager.emit)
        except Exception:  # noqa: BLE001 - never let the task die silently
            logger.exception("[main] background run %s crashed", run_id)
        finally:
            await run_manager.close(run_id)

    asyncio.create_task(_runner())
    return {"run_id": run_id}


@app.get("/api/research/{run_id}/stream")
async def stream_research(run_id: str):
    """Server-Sent Events stream of ProgressEvents for a run."""

    async def event_gen():
        async for event in run_manager.stream(run_id):
            yield f"data: {event.model_dump_json()}\n\n"
        # Final sentinel event so the client knows the stream is closed.
        yield 'data: {"stage": "done", "status": "closed", "message": "stream closed"}\n\n'

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable proxy buffering (nginx)
        },
    )


@app.get("/api/runs")
async def list_runs():
    return {"runs": await store.list_recent()}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    run = await store.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@app.get("/")
async def index():
    index_html = FRONTEND_DIR / "index.html"
    if not index_html.exists():
        return {"message": "Frontend not built yet. API is running."}
    return FileResponse(index_html)


# Serve static assets (JS/CSS) if the frontend dir exists.
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
