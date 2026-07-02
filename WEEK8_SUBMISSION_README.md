# Week 8 Final Submission — Cash Flow Runway Advisor

**Course:** Generative AI · **Track:** Claude Agent SDK (Track B) · **Group 4:** Xing Wang, Yi Lu, Aastik Rajan

This document is the entry point for the grader. It maps every rubric line item to where it lives in the repository.

---

## 0. What to open first

| You want to see… | Open this |
|---|---|
| The agent running end-to-end | `cash_flow_runway_advisor-5.ipynb` (run top to bottom in Colab) |
| Performance numbers | `PERFORMANCE_REPORT.md` |
| Architecture decisions + diagram | `ARCHITECTURE.md` |
| Failure post-mortem the demo video covers | `DEMO_VIDEO_SCRIPT.md` |
| Phase 1 proposal | `GAI Project_Cash Flow Runway Advisor Proposal.md` |
| Live web frontend (optional sidequest) | `python -m uvicorn app:app` then `http://localhost:8000` |

---

## 1. Rubric → Artifact Map

### 1. Technical Implementation (30 pts)
| Criterion | Where |
|---|---|
| Multi-agent orchestration (Planner + Executor) | `agent/orchestrator.py`; notebook §4, cell 11 |
| Manual `tool_use` loop (Track B requirement) | `run_executor()` in `agent/orchestrator.py` lines 360–479 |
| 3+ tools returning structured data | `agent/tools.py` — `query_financials`, `calculate_runway`, `simulate_scenario` |
| Hard guardrails (iterations / tokens / wall / tool timeout) | `Guardrails` dataclass; checks at lines 386–405 of `orchestrator.py` |
| Recovery / branching logic | Planner returns `execute` / `clarify` / `out_of_scope`; executor recovers from tool errors with `{status: 'error', available: [...]}` envelope |
| Telemetry scaffolding | `Trace` class — every planner thought, tool call, tool result, guardrail trip stamped with monotonic time. Persisted to `artifacts/` (notebook §16) |

### 2. Reasoning & Analytic Rigor (20 pts)
| Criterion | Where |
|---|---|
| ReAct trace / internal monologue | Executor system prompt v3 mandates "before each tool call, state what you expect" — see notebook §4 and §4b annotated trace |
| True analytical transformation | `calculate_runway` uses rolling 3-month burn σ for sensitivity bounds; `simulate_scenario` produces baseline vs scenario vs delta vs verdict |
| Prompt Version Control log (3+ iterations × 3 prompts) | Notebook §11 (cell 30) — Planner v1/v2/v3, Executor v1/v2/v3, Judge v1/v2/v3 each with failure mode and what fixed it |

### 3. Evaluation & QA (20 pts)
| Criterion | Where |
|---|---|
| 50+ synthetic test variants | Notebook §7 (cell 21) — `generate_synthetic_variants(... variants_per_seed=10)` over 5 seeds |
| LLM-as-Judge with custom rubric + citations | `JUDGE_SYSTEM_V3` in `orchestrator.py` lines 251–297. 3 standard + 3 custom KPIs, citations mandatory, JSON-only output |
| Consistency Score (10 cases × 3 runs, variance reported) | Notebook §10 (cell 29) |
| Red-team findings + mitigations | Notebook §14 (cell 33) — out-of-range date, binary pressure, hallucinated numbers, wrong casing, missing parameter, infinite loop |

### 4. FinOps & Performance (15 pts)
| Criterion | Where |
|---|---|
| Cost-per-success vs cost-per-failure | Notebook §15 (cell 35) and `PERFORMANCE_REPORT.md` §3 |
| High-perf (Sonnet) vs Budget (Haiku) split | ADR-1 in `ARCHITECTURE.md`; orchestrator uses Sonnet for Planner+Judge, Haiku for Executor |
| Latency profile (inference vs tool seconds) | `CostTracker.inference_seconds` vs `tool_seconds`; `PERFORMANCE_REPORT.md` §4 |
| 3+ domain KPIs | Judge custom rubric: `calculation_accuracy`, `scenario_completeness`, `risk_disclosure` |

### 5. Documentation & Justification (15 pts)
| Criterion | Where |
|---|---|
| 4 ADRs (Model / AI-vs-Rules / State / Errors) | `ARCHITECTURE.md` §1–4 (also notebook §12) |
| Mermaid architecture diagram | `ARCHITECTURE.md` §5 (also notebook §13) |
| Failure post-mortem | Notebook §14 + the 5-min demo video (`DEMO_VIDEO_SCRIPT.md`) |

---

## 2. Auto-Deduction Checklist (we avoided all three)

- [x] **−10 chatbot UI for demo:** the demo runs the `run_task()` orchestrator from the notebook, not a chat window.
- [x] **−10 hard-coded answers:** every numeric claim is derived from a tool call; success classifier rejects anything without an `<answer>` block.
- [x] **−5 exposed API keys:** `app.py` and notebook pull `ANTHROPIC_API_KEY` from Colab `userdata` or env — no literal keys in code.

---

## 3. Reproducing the run

```bash
# In Colab: open cash_flow_runway_advisor-5.ipynb, set ANTHROPIC_API_KEY in
# the secrets sidebar, Run All. Total wall time ~12–18 min, cost ~$0.40–0.60.

# Local:
pip install anthropic==0.77.1 pandas fastapi uvicorn
export ANTHROPIC_API_KEY=sk-ant-...
jupyter notebook cash_flow_runway_advisor-5.ipynb
```

Artifacts are written to `artifacts/<timestamp>/` (traces, judge verdicts, aggregate report). The synthetic dataset is reproducible from `SEED=42` in `agent/data.py`, so a re-run gives identical ground truth.
