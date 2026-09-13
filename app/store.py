"""SQLite persistence for completed research runs.

Only final artifacts are persisted (question, plan, findings, report) so
you can reload a past run. Live progress stays in-memory/streamed and is
not stored. The schema is deliberately simple: one row per run, with the
structured objects stored as JSON blobs.

All DB calls run in a thread (asyncio.to_thread) because sqlite3 is
synchronous; that keeps the event loop responsive on the free-tier box.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Optional

from .config import settings
from .schemas import ResearchPlan, ResearchBundle, SynthesizedReport

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    question      TEXT NOT NULL,
    status        TEXT NOT NULL,          -- 'completed' | 'failed'
    plan_json     TEXT,
    bundle_json   TEXT,
    report_json   TEXT,
    error         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class RunStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or settings.sqlite_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ── writes ────────────────────────────────────────────────────────
    def _save_sync(
        self,
        *,
        run_id: str,
        question: str,
        status: str,
        plan: Optional[ResearchPlan],
        bundle: Optional[ResearchBundle],
        report: Optional[SynthesizedReport],
        error: Optional[str],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO runs
                   (run_id, question, status, plan_json, bundle_json, report_json, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    question,
                    status,
                    plan.model_dump_json() if plan else None,
                    bundle.model_dump_json() if bundle else None,
                    report.model_dump_json() if report else None,
                    error,
                ),
            )

    async def save(
        self,
        *,
        run_id: str,
        question: str,
        status: str,
        plan: Optional[ResearchPlan] = None,
        bundle: Optional[ResearchBundle] = None,
        report: Optional[SynthesizedReport] = None,
        error: Optional[str] = None,
    ) -> None:
        await asyncio.to_thread(
            self._save_sync,
            run_id=run_id,
            question=question,
            status=status,
            plan=plan,
            bundle=bundle,
            report=report,
            error=error,
        )

    # ── reads ─────────────────────────────────────────────────────────
    def _get_sync(self, run_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            return _row_to_dict(row) if row else None

    async def get(self, run_id: str) -> Optional[dict]:
        return await asyncio.to_thread(self._get_sync, run_id)

    def _list_sync(self, limit: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT run_id, question, status, created_at FROM runs "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    async def list_recent(self, limit: int = 20) -> list[dict]:
        return await asyncio.to_thread(self._list_sync, limit)


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in ("plan_json", "bundle_json", "report_json"):
        if d.get(key):
            d[key.replace("_json", "")] = json.loads(d[key])
        d.pop(key, None)
    return d
