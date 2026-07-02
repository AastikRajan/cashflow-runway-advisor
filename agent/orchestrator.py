from __future__ import annotations
from .tools import TOOL_SCHEMAS, dispatch
"""Multi-agent orchestrator: Planner (Sonnet) -> Executor (Haiku) -> Answer.

Track B / Claude Agent SDK style: we manually drive the tool_use loop instead
of using a framework. The loop is deterministic and bounded.

Guardrails (hard kill switches):
  - max_iterations: caps the executor's tool_use cycles.
  - max_total_tokens: caps cumulative input+output tokens per task.
  - tool_timeout_s: per-tool wall clock cap.

Telemetry:
  - Every step (planner thought, tool call, tool result, model message, error)
    is appended to a Trace with monotonic timestamps. Persisted as JSON for
    the Judge agent and the FinOps report.

FinOps:
  - CostTracker estimates $ from input/output token counts using a per-model
    pricing table. Numbers are estimates; the user can swap in real list
    prices in PRICES.
"""


import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from anthropic import Anthropic

# ----------------------------------------------------------------------------
# Pricing (USD per 1M tokens) -- swap with current list prices as needed.
# ----------------------------------------------------------------------------
PRICES = {
    "claude-opus-4-7":            {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-6":          {"input": 3.0,  "output": 15.0},
    "claude-haiku-4-5-20251001":  {"input": 1.0,  "output": 5.0},
    # Fallback for anything else:
    "_default":                   {"input": 3.0,  "output": 15.0},
}

PLANNER_MODEL  = "claude-sonnet-4-6"
EXECUTOR_MODEL = "claude-haiku-4-5-20251001"
JUDGE_MODEL    = "claude-sonnet-4-6"

# ----------------------------------------------------------------------------
# Telemetry
# ----------------------------------------------------------------------------

@dataclass
class TraceStep:
    ts: float
    kind: str          # "planner" | "executor_msg" | "tool_call" | "tool_result" | "answer" | "error" | "guardrail"
    payload: dict

@dataclass
class Trace:
    task_id: str
    user_question: str
    steps: list[TraceStep] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    on_event: "Any | None" = field(default=None, repr=False)

    def add(self, kind: str, **payload):
        self.steps.append(TraceStep(ts=time.time() - self.started_at, kind=kind, payload=payload))
        if self.on_event:
            try:
                self.on_event({"type": kind, **payload})
            except Exception:
                pass

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "user_question": self.user_question,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "wall_seconds": (self.finished_at or time.time()) - self.started_at,
            "steps": [s.__dict__ for s in self.steps],
        }

# ----------------------------------------------------------------------------
# Cost tracking
# ----------------------------------------------------------------------------

@dataclass
class CostTracker:
    by_model: dict[str, dict[str, int]] = field(default_factory=dict)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    inference_seconds: float = 0.0
    tool_seconds: float = 0.0

    def record_call(self, model: str, input_tokens: int, output_tokens: int, latency_s: float):
        bucket = self.by_model.setdefault(model, {"input": 0, "output": 0, "calls": 0, "seconds": 0.0})
        bucket["input"] += input_tokens
        bucket["output"] += output_tokens
        bucket["calls"] += 1
        bucket["seconds"] += latency_s
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.inference_seconds += latency_s
        price = PRICES.get(model, PRICES["_default"])
        self.total_cost_usd += (input_tokens / 1e6) * price["input"]
        self.total_cost_usd += (output_tokens / 1e6) * price["output"]

    def record_tool(self, seconds: float):
        self.tool_seconds += seconds

    def snapshot(self) -> dict:
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "inference_seconds": round(self.inference_seconds, 3),
            "tool_seconds": round(self.tool_seconds, 3),
            "by_model": self.by_model,
        }

# ----------------------------------------------------------------------------
# Guardrails
# ----------------------------------------------------------------------------

@dataclass
class Guardrails:
    max_iterations: int = 7
    max_total_tokens: int = 60_000
    tool_timeout_s: float = 10.0
    max_wall_seconds: float = 60.0

class GuardrailBreach(Exception):
    pass

def _run_with_timeout(fn: Callable, timeout: float):
    """Run fn() with a wall-clock timeout. Returns (result, error)."""
    box: dict[str, Any] = {}
    def runner():
        try:
            box["result"] = fn()
        except Exception as e:
            box["error"] = e
    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None, TimeoutError(f"tool exceeded {timeout}s")
    if "error" in box:
        return None, box["error"]
    return box.get("result"), None

# ----------------------------------------------------------------------------
# Prompt registry (Prompt Version Control)
# ----------------------------------------------------------------------------

# v3 = current. v1, v2 kept in PVC_HISTORY (see notebook) for documentation.
PLANNER_SYSTEM_V3 = """You are the PLANNER agent of a Cash Flow Runway Advisor.

Your job is NOT to answer the user. Your job is to produce a short, structured
plan that the EXECUTOR agent will follow.

You have access to (but DO NOT call) these tools:
  - query_financials(sql): read-only SQL on invoices, expenses, cash_snapshots, customers
  - calculate_runway(...): deterministic runway math with sensitivity bounds
  - simulate_scenario(action, params, timeframe_months): named what-if comparisons
    actions: hire, delay_hire, cut_spend, ar_delay, price_increase, raise_capital

DECISION RULES (apply in order):
  1. If the user's question is ambiguous on a value that materially changes
     the answer (e.g. unspecified salary, unspecified delay days, unspecified
     timeframe), produce a plan with action="clarify" listing 1-3 concrete
     questions. Do NOT invent numbers.
  2. If the question asks about a date range outside the dataset, produce a
     plan with action="out_of_scope" and explain.
  3. Otherwise produce action="execute" with an ordered list of steps.
     Each step references one of the tools above and a brief intent.
  4. Always include a final synthesis step where the executor compares results
     and writes a recommendation with explicit risk disclosure.
  5. If the question is conditional ("if X happens..." / "what if X?") and X is
     a tool-measurable variable (AR delay, headcount, price change), plan BOTH
     the conditional scenario AND the no-action baseline. Populate
     scenarios_to_compare accordingly. Never answer a conditional with a single
     scenario — the comparison is the answer.

OUTPUT FORMAT (valid JSON, no prose outside the JSON):
{
  "action": "execute" | "clarify" | "out_of_scope",
  "rationale": "<1-2 sentences on why this plan>",
  "clarifying_questions": ["..."]   // only if action=clarify
  "out_of_scope_reason": "..."       // only if action=out_of_scope
  "steps": [                          // only if action=execute
    {"tool": "query_financials", "intent": "..."},
    {"tool": "calculate_runway",  "intent": "..."},
    {"tool": "simulate_scenario", "intent": "...", "action_hint": "hire"},
    ...
    {"tool": "synthesize", "intent": "compare results, flag risks, recommend"}
  ],
  "scenarios_to_compare": ["baseline", "hire_q2", "ar_delay_45d"]   // optional
}
""".strip()

EXECUTOR_SYSTEM_V3 = """You are the EXECUTOR agent of a Cash Flow Runway Advisor.

You will receive:
  (1) the user's original question
  (2) a JSON plan from the PLANNER

You execute the plan by calling the provided tools. Follow these rules:

NUMERIC INTEGRITY
  - NEVER compute a runway figure yourself. Always derive numeric claims from
    tool output. If you state a number that isn't in a tool result, you have
    failed.
  - All currency is USD. Round to whole dollars in prose; keep cents in tools.

TOOL USE
  - Prefer simulate_scenario for named what-ifs (hire, cut_spend, ar_delay).
  - Use calculate_runway when you need a custom delta combination.
  - Use query_financials when you need raw rows (top customers, AR aging).
  - If a tool returns status="error", read the error and either retry with
    fixed inputs or report the gap to the user. Do not fabricate a result.

REASONING TRANSPARENCY (ReAct style)
  - Before each tool call, state in one sentence what you expect the result
    to show and why. After the tool returns, state in one sentence what you
    actually observed and what you'll do next.

FINAL ANSWER FORMAT
  - Wrap the final answer in <answer>...</answer> tags. Inside:
      * One-sentence direct answer to the user's question.
      * A "Numbers" block with each runway figure cited from a tool.
      * A "Trade-offs" block comparing scenarios (if multiple).
      * A "Risks" block listing at least one of: AR concentration risk,
        burn variance, one-time vs recurring effects, data freshness.
      * A "Recommendation" block: 1-3 sentences. If runway is below the
        6-month safety threshold, the recommendation MUST explicitly flag it.
  - If the planner returned action="clarify", do NOT call tools. Output the
    clarifying questions verbatim wrapped in <clarify>...</clarify>.
  - If action="out_of_scope", explain inside <out_of_scope>...</out_of_scope>.

CONSERVATIVE BIAS
  - You are advising on irreversible decisions. When in doubt, surface
    uncertainty rather than smoothing over it.
""".strip()

JUDGE_SYSTEM_V3 = """You are a senior FP&A analyst grading a junior financial analyst agent.

You will receive:
  - The user's original question
  - The agent's full reasoning trace (planner output, tool calls, tool outputs)
  - The agent's final answer
  - Ground-truth values for this synthetic test case (when available)

Score the agent on a 1-5 scale across the following dimensions.

STANDARD RUBRIC
  1. instruction_adherence  -- did the agent address the user's actual question?
  2. reasoning_transparency -- is the trace legible and logical?
  3. hallucination_check    -- are all numeric claims backed by tool output?

CUSTOM DOMAIN RUBRIC
  4. calculation_accuracy   -- is the runway figure within +/-5% of ground truth
                               (or 'n/a' if the case has no ground truth)?
  5. scenario_completeness  -- "Required scenarios" are listed as
                               scenarios_to_compare in the PLANNER OUTPUT section
                               below. Count how many appear in the <answer> Numbers
                               block with an explicit runway figure.
                               Score = min(5, ceil(found/required * 5)).
                               If required=0, grade holistically.
  6. risk_disclosure        -- Check the <answer> Risks block for these items:
                               AR_VARIANCE, CUSTOMER_CONCENTRATION,
                               ONE_TIME_EXPENSE, SEASONALITY,
                               RUNWAY_BELOW_THRESHOLD.
                               Score 5 if >= 2 present, 3 if exactly 1, 1 if none.
                               Cite the exact phrase(s) found.

For EACH score you MUST cite the specific trace step (e.g. "Executor tool_call #2"
or "Final <answer> Numbers block") that justifies the score. Do NOT award full
marks without a citation.

Return output as VALID JSON ONLY (no prose outside the JSON) matching this schema:
{
  "instruction_adherence":  {"score": 1-5, "citation": "..."},
  "reasoning_transparency": {"score": 1-5, "citation": "..."},
  "hallucination_check":    {"score": 1-5, "citation": "..."},
  "calculation_accuracy":   {"score": 1-5, "citation": "..."},
  "scenario_completeness":  {"score": 1-5, "citation": "..."},
  "risk_disclosure":        {"score": 1-5, "citation": "..."},
  "overall_verdict": "PASS" | "PARTIAL" | "FAIL",
  "notes": "one-sentence summary"
}
""".strip()

# ----------------------------------------------------------------------------
# Planner
# ----------------------------------------------------------------------------

def run_planner(
    client: Anthropic,
    user_question: str,
    trace: Trace,
    cost: CostTracker,
    model: str = PLANNER_MODEL,
    max_tokens: int = 1024,
) -> dict:
    t0 = time.time()
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": PLANNER_SYSTEM_V3,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_question}],
    )
    dt = time.time() - t0
    cost.record_call(model, resp.usage.input_tokens, resp.usage.output_tokens, dt)

    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    plan = _parse_plan_json(text)

    trace.add("planner",
              model=model,
              raw_text=text,
              parsed_plan=plan,
              input_tokens=resp.usage.input_tokens,
              output_tokens=resp.usage.output_tokens,
              latency_s=round(dt, 3))
    return plan

def _parse_plan_json(text: str) -> dict:
    # Tolerate JSON wrapped in code fences.
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:]
        s = s.strip()
    try:
        return json.loads(s)
    except Exception:
        # Best-effort: find first { and last }
        i, j = s.find("{"), s.rfind("}")
        if i != -1 and j != -1 and j > i:
            try:
                return json.loads(s[i:j+1])
            except Exception:
                pass
        return {"action": "execute", "rationale": "fallback (planner JSON unparseable)",
                "steps": [{"tool": "synthesize", "intent": "answer best-effort"}],
                "_unparseable": text[:1000]}

# ----------------------------------------------------------------------------
# Executor (drives the tool_use loop)
# ----------------------------------------------------------------------------

def run_executor(
    client: Anthropic,
    conn: sqlite3.Connection,
    user_question: str,
    plan: dict,
    trace: Trace,
    cost: CostTracker,
    guardrails: Guardrails,
    model: str = EXECUTOR_MODEL,
    max_tokens: int = 1500,
) -> dict:
    """Run the tool_use loop until the executor produces a final answer or
    a guardrail trips. Returns {"final_text": ..., "stopped_reason": ...}."""

    user_msg = (
        f"USER QUESTION:\n{user_question}\n\n"
        f"PLANNER OUTPUT (JSON):\n{json.dumps(plan, indent=2)}"
    )
    messages: list[dict] = [{"role": "user", "content": user_msg}]
    iterations = 0
    stop_reason = None
    final_text = ""
    started = time.time()

    def _force_final_synthesis() -> str:
        """When a guardrail trips, do ONE last call asking the executor to write
        an <answer> block from whatever data it already gathered. Tools disabled.
        This rescues the run from showing nothing to the user."""
        synth_msgs = messages + [{
            "role": "user",
            "content": (
                "GUARDRAIL: you have used up your tool-call budget. STOP calling "
                "tools. Using ONLY the tool results already in this conversation, "
                "produce the final <answer>...</answer> block now with Numbers / "
                "Trade-offs / Risks / Recommendation. If a number isn't available, "
                "say so explicitly — do not invent values."
            ),
        }]
        try:
            t0 = time.time()
            r = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": EXECUTOR_SYSTEM_V3,
                         "cache_control": {"type": "ephemeral"}}],
                messages=synth_msgs,
            )
            dt = time.time() - t0
            cost.record_call(model, r.usage.input_tokens, r.usage.output_tokens, dt)
            return _extract_text(r.content)
        except Exception as e:
            trace.add("error", stage="forced_synthesis", message=str(e))
            return ""

    while True:
        iterations += 1

        # ---- guardrail: max iterations
        if iterations > guardrails.max_iterations:
            stop_reason = "max_iterations"
            trace.add("guardrail", reason=stop_reason, iterations=iterations)
            final_text = _force_final_synthesis()
            break

        # ---- guardrail: token budget
        if cost.total_input_tokens + cost.total_output_tokens > guardrails.max_total_tokens:
            stop_reason = "token_budget"
            trace.add("guardrail", reason=stop_reason,
                      tokens_so_far=cost.total_input_tokens + cost.total_output_tokens)
            final_text = _force_final_synthesis()
            break

        # ---- guardrail: wall clock
        if time.time() - started > guardrails.max_wall_seconds:
            stop_reason = "wall_clock"
            trace.add("guardrail", reason=stop_reason,
                      seconds=round(time.time() - started, 2))
            final_text = _force_final_synthesis()
            break

        t0 = time.time()
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": EXECUTOR_SYSTEM_V3,
                     "cache_control": {"type": "ephemeral"}}],
            tools=TOOL_SCHEMAS,
            messages=messages,
        )
        dt = time.time() - t0
        cost.record_call(model, resp.usage.input_tokens, resp.usage.output_tokens, dt)
        trace.add("executor_msg",
                  iteration=iterations,
                  stop_reason=resp.stop_reason,
                  input_tokens=resp.usage.input_tokens,
                  output_tokens=resp.usage.output_tokens,
                  latency_s=round(dt, 3),
                  content_summary=_summarize_blocks(resp.content))

        if resp.stop_reason == "end_turn":
            final_text = _extract_text(resp.content)
            stop_reason = "end_turn"
            break

        if resp.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})

            tool_results = []
            for block in resp.content:
                if getattr(block, "type", "") != "tool_use":
                    continue
                name = block.name
                tool_input = block.input or {}
                trace.add("tool_call", tool=name, input=tool_input, tool_use_id=block.id)

                t_tool = time.time()
                result, err = _run_with_timeout(
                    lambda n=name, ti=tool_input: dispatch(conn, n, ti),
                    guardrails.tool_timeout_s,
                )
                tool_dt = time.time() - t_tool
                cost.record_tool(tool_dt)

                if err is not None:
                    payload = {"status": "error", "error": str(err), "exception_type": type(err).__name__}
                else:
                    payload = result

                trace.add("tool_result",
                          tool=name,
                          tool_use_id=block.id,
                          latency_s=round(tool_dt, 3),
                          result_preview=_preview(payload))

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(payload, default=str),
                    "is_error": payload.get("status") == "error",
                })

            messages.append({"role": "user", "content": tool_results})
            continue

        # other stop reasons (max_tokens, etc) - bail
        stop_reason = resp.stop_reason or "unknown"
        final_text = _extract_text(resp.content)
        trace.add("error", stop_reason=stop_reason, partial=final_text[:200])
        break

    trace.add("answer", stopped_reason=stop_reason, iterations=iterations,
              final_text=final_text)
    return {"final_text": final_text, "stopped_reason": stop_reason, "iterations": iterations}

def _extract_text(blocks) -> str:
    parts = []
    for b in blocks:
        if getattr(b, "type", "") == "text":
            parts.append(b.text)
    return "\n".join(parts).strip()

def _summarize_blocks(blocks) -> list[dict]:
    out = []
    for b in blocks:
        t = getattr(b, "type", "")
        if t == "text":
            out.append({"type": "text", "chars": len(b.text)})
        elif t == "tool_use":
            out.append({"type": "tool_use", "name": b.name, "input_keys": list((b.input or {}).keys())})
        else:
            out.append({"type": t})
    return out

def _preview(payload: dict, max_chars: int = 600) -> str:
    s = json.dumps(payload, default=str)
    return s if len(s) <= max_chars else s[:max_chars] + "...<truncated>"

# ----------------------------------------------------------------------------
# Public entry point: run a single task end-to-end
# ----------------------------------------------------------------------------

@dataclass
class TaskResult:
    task_id: str
    user_question: str
    plan: dict
    final_text: str
    stopped_reason: str
    iterations: int
    trace: Trace
    cost: CostTracker
    success: bool
    success_reason: str

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "user_question": self.user_question,
            "plan": self.plan,
            "final_text": self.final_text,
            "stopped_reason": self.stopped_reason,
            "iterations": self.iterations,
            "success": self.success,
            "success_reason": self.success_reason,
            "trace": self.trace.to_dict(),
            "cost": self.cost.snapshot(),
        }

def run_task(
    client: Anthropic,
    conn: sqlite3.Connection,
    user_question: str,
    guardrails: Guardrails | None = None,
    task_id: str | None = None,
) -> TaskResult:
    """End-to-end: planner -> executor -> structured TaskResult."""
    guardrails = guardrails or Guardrails()
    task_id = task_id or uuid.uuid4().hex[:8]
    trace = Trace(task_id=task_id, user_question=user_question)
    cost = CostTracker()

    try:
        plan = run_planner(client, user_question, trace, cost)
    except Exception as e:
        trace.add("error", stage="planner", message=str(e))
        trace.finished_at = time.time()
        return TaskResult(task_id, user_question, {"action": "error"}, f"Planner failed: {e}",
                          "planner_error", 0, trace, cost, False, "planner_exception")

    if plan.get("action") == "clarify":
        qs = plan.get("clarifying_questions", [])
        text = "<clarify>\n" + "\n".join(f"- {q}" for q in qs) + "\n</clarify>"
        trace.add("answer", stopped_reason="clarify", iterations=0, final_text=text)
        trace.finished_at = time.time()
        return TaskResult(task_id, user_question, plan, text, "clarify",
                          0, trace, cost, True, "clarify_appropriate")

    if plan.get("action") == "out_of_scope":
        text = f"<out_of_scope>\n{plan.get('out_of_scope_reason','')}\n</out_of_scope>"
        trace.add("answer", stopped_reason="out_of_scope", iterations=0, final_text=text)
        trace.finished_at = time.time()
        return TaskResult(task_id, user_question, plan, text, "out_of_scope",
                          0, trace, cost, True, "out_of_scope_appropriate")

    try:
        ex = run_executor(client, conn, user_question, plan, trace, cost, guardrails)
    except Exception as e:
        trace.add("error", stage="executor", message=str(e))
        trace.finished_at = time.time()
        return TaskResult(task_id, user_question, plan, f"Executor failed: {e}",
                          "executor_error", 0, trace, cost, False, "executor_exception")

    success, success_reason = _classify_success(ex["final_text"], ex["stopped_reason"])
    trace.finished_at = time.time()
    return TaskResult(
        task_id=task_id,
        user_question=user_question,
        plan=plan,
        final_text=ex["final_text"],
        stopped_reason=ex["stopped_reason"],
        iterations=ex["iterations"],
        trace=trace,
        cost=cost,
        success=success,
        success_reason=success_reason,
    )

def _classify_success(final_text: str, stopped_reason: str) -> tuple[bool, str]:
    if stopped_reason == "end_turn" and "<answer>" in final_text and "</answer>" in final_text:
        return True, "answer_block_present"
    if stopped_reason in {"max_iterations", "token_budget", "wall_clock"}:
        return False, f"guardrail_{stopped_reason}"
    if stopped_reason == "end_turn":
        return False, "no_answer_block"
    return False, f"stopped_{stopped_reason}"
