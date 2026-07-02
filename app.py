"""Cash Flow Runway Advisor – FastAPI web application.

Wraps the multi-agent orchestrator in a streaming HTTP API so the frontend
can show tool calls in real time.  The agent is synchronous; we run it on
a thread-pool worker and push events through an asyncio.Queue.

Routes
------
POST /api/chat          stream SSE events for one user question
GET  /api/status        current runway snapshot (no API call)
GET  /                  serves static/index.html
GET  /static/{path}     serves static assets
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from contextlib import asynccontextmanager
from typing import Any

from anthropic import Anthropic
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.data import generate
from agent.tools import calculate_runway, simulate_scenario
from agent.orchestrator import (
    Guardrails, Trace, CostTracker, TaskResult,
    run_planner, run_executor,
    PLANNER_SYSTEM_V3, EXECUTOR_SYSTEM_V3,
    _classify_success,
)

# ---------------------------------------------------------------------------
# Shared state (created once on startup)
# ---------------------------------------------------------------------------
_client: Anthropic | None = None
_conn = None          # sqlite3.Connection — synthetic OrbitSaaS DB
_baseline: dict = {}  # cached runway snapshot for /api/status

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _client, _conn, _baseline
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    _client = Anthropic(api_key=api_key) if api_key else Anthropic()
    ds = generate()
    _conn = ds.to_sqlite()
    _baseline = _compute_baseline()
    yield
    if _conn:
        _conn.close()

app = FastAPI(title="Cash Flow Runway Advisor", lifespan=lifespan)

# Serve static files (the HTML frontend)
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


def _compute_baseline() -> dict:
    r = calculate_runway(_conn)
    if r.get("status") != "ok":
        return {}
    sc = simulate_scenario(_conn, "hire", {"salary_annual": 140_000})
    return {
        "runway_months": r.get("runway_months"),
        "monthly_net_burn": r.get("monthly_net_burn"),
        "cash_balance": r.get("cash_balance"),
        "below_safety_threshold": r.get("below_safety_threshold"),
        "hire_140k_runway": sc.get("scenario", {}).get("runway_months"),
    }

# ---------------------------------------------------------------------------
# Streaming agent runner
# ---------------------------------------------------------------------------

def _run_agent_thread(
    question: str,
    guardrails: Guardrails,
    task_id: str,
    event_queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Runs synchronously in a thread; pushes events into the async queue."""

    def push(event: dict) -> None:
        loop.call_soon_threadsafe(event_queue.put_nowait, event)

    # Instrument Trace so every .add() fires a push()
    class StreamTrace(Trace):
        def add(self, kind: str, **payload: Any) -> None:
            super().add(kind, **payload)
            push({"type": kind, **_safe_payload(payload)})

    cost = CostTracker()
    trace = StreamTrace(task_id=task_id, user_question=question)

    try:
        plan = run_planner(_client, question, trace, cost)
        action = plan.get("action", "execute")

        if action == "clarify":
            push({
                "type": "clarify",
                "questions": plan.get("clarifying_questions", []),
            })
        elif action == "out_of_scope":
            push({
                "type": "out_of_scope",
                "reason": plan.get("out_of_scope_reason", ""),
            })
        else:
            ex = run_executor(_client, _conn, question, plan, trace, cost, guardrails)
            success, _ = _classify_success(ex["final_text"], ex["stopped_reason"])
            push({
                "type": "answer",
                "text": ex["final_text"],
                "stopped_reason": ex["stopped_reason"],
                "success": success,
            })

        snap = cost.snapshot()
        push({
            "type": "done",
            "cost_usd": round(snap["total_cost_usd"], 5),
            "input_tokens": snap["total_input_tokens"],
            "output_tokens": snap["total_output_tokens"],
            "latency_s": round(snap["inference_seconds"] + snap["tool_seconds"], 2),
        })
    except Exception as exc:
        push({"type": "error", "message": str(exc)})
    finally:
        loop.call_soon_threadsafe(event_queue.put_nowait, None)  # sentinel


def _safe_payload(payload: dict) -> dict:
    """Strip large non-serialisable fields before sending over SSE."""
    skip = {"input_tokens", "output_tokens"}
    out: dict = {}
    for k, v in payload.items():
        if k in skip:
            continue
        try:
            json.dumps(v)
            out[k] = v
        except (TypeError, ValueError):
            out[k] = str(v)[:200]
    return out


async def _stream_events(question: str) -> AsyncGenerator:
    loop = asyncio.get_event_loop()
    q: asyncio.Queue = asyncio.Queue()
    task_id = uuid.uuid4().hex[:8]
    # Web frontend uses more generous guardrails than the notebook so a single
    # exploratory question can branch through several scenarios without truncating.
    guardrails = Guardrails(
        max_iterations=14,
        max_total_tokens=180_000,
        max_wall_seconds=180.0,
        tool_timeout_s=15.0,
    )

    t = threading.Thread(
        target=_run_agent_thread,
        args=(question, guardrails, task_id, q, loop),
        daemon=True,
    )
    t.start()

    while True:
        try:
            event = await asyncio.wait_for(q.get(), timeout=120.0)
        except asyncio.TimeoutError:
            yield f"data: {json.dumps({'type': 'error', 'message': 'timeout'})}\n\n"
            break
        if event is None:
            break
        yield f"data: {json.dumps(event)}\n\n"


# Fix missing AsyncGenerator import
from typing import AsyncGenerator

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    question: str


@app.post("/api/chat")
async def chat(body: ChatRequest):
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    return StreamingResponse(
        _stream_events(question),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/status")
async def status():
    return _baseline


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = os.path.join(static_dir, "index.html")
    if not os.path.isfile(html_path):
        return HTMLResponse("<h1>Frontend not found — place index.html in static/</h1>", status_code=404)
    with open(html_path, encoding="utf-8") as f:
        return HTMLResponse(f.read())
