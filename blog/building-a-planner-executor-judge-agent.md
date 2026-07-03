---
title: "Planner, Executor, Judge: a multi-agent pattern that actually stays on the rails"
published: false
tags: ai, llm, python, tutorial
canonical_url:
---

# Planner, Executor, Judge: a multi-agent pattern that actually stays on the rails

Single-prompt LLM apps fall apart the moment a question needs *work* — pull data, compute something, check the answer. You bolt on tools, then the model loops forever, blows your token budget, or confidently invents a number.

Building **Cash Flow Runway Advisor** (a finance assistant that answers *"what's my runway if I hire two engineers?"*), we landed on a three-role split that kept it honest. Here's the pattern.

**Code:** https://github.com/AastikRajan/cashflow-runway-advisor
*(Course project — team: Xing Wang, Yi Lu, Aastik Rajan.)*

## The three roles

```
Question → [Planner] → decide: execute / clarify / out-of-scope
                          │
                          ▼
                     [Executor] → manual tool_use loop over structured tools
                          │
                          ▼
                      [Judge]   → is this answer grounded and correct?
                          │
                          ▼
                    streamed answer + full trace
```

- **Planner (a stronger model)** classifies intent *before* any tool runs. Most bad agent runs come from executing a question that should have been a clarification. Cheap to catch here.
- **Executor (a cheaper, faster model)** runs the actual `tool_use` loop over three tools that return **structured data**: `query_financials`, `calculate_runway`, `simulate_scenario`. Fast model, tight job.
- **Judge (a stronger model again)** scores the final answer for grounding. It's the difference between "sounds right" and "is right."

Using a small model for the busy middle role and larger models for the two judgment calls keeps both cost and latency sane.

## The part everyone skips: guardrails

An agent without limits is a way to spend money at cloud speed. Every Executor loop is bounded by a single dataclass:

```python
@dataclass
class Guardrails:
    max_iterations: int      # stop runaway tool loops
    max_tokens: int          # hard budget ceiling
    max_wall_seconds: float  # real-time cutoff
    tool_timeout_seconds: float
```

Checked on *every* iteration. When a tool errors, the Executor doesn't crash — it gets a structured envelope back and recovers:

```python
{"status": "error", "available": ["query_financials", "calculate_runway"]}
```

so it can re-plan instead of hallucinating.

## Make the analytics real

The easy trap is a "tool" that just reformats the prompt. Ours actually compute:

- `calculate_runway` uses a **rolling 3-month burn σ** to give sensitivity bounds, not a single point estimate.
- `simulate_scenario` returns **baseline vs scenario vs delta vs verdict** — a real comparison, not prose.

If your tool's output could have been written by the LLM alone, it isn't a tool.

## Trace everything

Every planner thought, tool call, tool result, and guardrail trip is stamped with a monotonic timestamp and persisted. When an answer looks wrong, you *read the run* instead of guessing. This one habit turned debugging from vibes into forensics.

## Takeaways

1. **Split judgment from labor.** Strong models decide, cheap model does.
2. **Guardrails are a feature, not a safety net.** Design them first.
3. **A tool that doesn't transform data isn't a tool.**
4. **If it isn't traced, it isn't debuggable.**

Full architecture write-up and evaluation numbers are in the repo: https://github.com/AastikRajan/cashflow-runway-advisor
