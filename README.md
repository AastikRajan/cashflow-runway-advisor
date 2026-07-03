# Cash Flow Runway Advisor

A multi-agent financial-planning assistant that answers questions like *"what's my current runway?"* and *"what happens if I hire two engineers?"* by reasoning over real financial data — not by guessing.

Built on a **Planner → Executor → Judge** agent loop with hard guardrails and full telemetry, served through a streaming FastAPI web app.

> 🎓 Course project for **Generative AI** (Claude Agent SDK track). Team: **Xing Wang, Yi Lu, Aastik Rajan.**

## How it works

```
User question
   │
   ▼
┌──────────┐   decides: execute / clarify / out-of-scope
│ Planner  │   (Claude Sonnet)
└────┬─────┘
     ▼
┌──────────┐   manual tool_use loop over 3 structured tools:
│ Executor │   • query_financials   • calculate_runway   • simulate_scenario
└────┬─────┘   (Claude Haiku) — recovers from tool errors, respects guardrails
     ▼
┌──────────┐   scores the answer for grounding & correctness
│  Judge   │   (Claude Sonnet)
└────┬─────┘
     ▼
  Streamed answer  ── every planner thought, tool call & guardrail trip is traced
```

- **Hard guardrails** — caps on iterations, tokens, wall-clock, and per-tool timeout (`Guardrails` dataclass).
- **Real analytics** — `calculate_runway` uses a rolling 3-month burn σ for sensitivity bounds; `simulate_scenario` returns baseline vs scenario vs delta vs verdict.
- **Telemetry** — a `Trace` records every step with monotonic timestamps.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full design and [`PERFORMANCE_REPORT.md`](PERFORMANCE_REPORT.md) for the evaluation matrix.

## Run it locally

```bash
pip install -r requirements_web.txt
export ANTHROPIC_API_KEY=sk-ant-...      # your key; never commit it
python -m uvicorn app:app --host 127.0.0.1 --port 8000
# open http://localhost:8000
```

Or run `cash_flow_runway_advisor-5.ipynb` top-to-bottom in Colab to see the agent end-to-end.

## Tech
Python · FastAPI · Server-Sent Events (streaming) · SQLite · Anthropic Claude (Sonnet + Haiku)

## License
MIT
