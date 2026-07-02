# Demo Video Script — 5-minute Failure Post-Mortem

**Rubric requirement:** "A 5-minute deep-dive into **one specific architectural failure** and how you solved it."
**Auto-deduction:** −10 pts if the demo uses a chatbot UI instead of agent logic. → We screenshare the **notebook** and the **trace JSON**, not the FastAPI frontend.

The failure we present: **Executor hallucinated runway numbers under combined scenarios** — the most consequential bug we caught, and the one whose fix touches three layers of the architecture.

---

## Minute 0:00–0:30 — Hook & framing

**On screen:** Title slide → cell 0 of the notebook.

**Script.**
> "We built a Cash Flow Runway Advisor — a multi-agent system that helps a CFO answer hire-or-wait questions in two minutes instead of two hours. The most dangerous failure we hit during development wasn't a crash. It was a beautifully-formatted answer with the wrong number in it. This is how we found it and what it cost us to fix."

---

## Minute 0:30–1:30 — The failure

**On screen:** Open `artifacts/<ts>/traces/` from an early run (or recreate by setting `EXECUTOR_SYSTEM` back to v1/v2 from notebook §11). Show a trace where the executor stated a runway of e.g. "≈4.2 months under hire + AR delay" with no corresponding `tool_call` in the steps.

**Script.**
> "Here's the failure. The agent's `<answer>` says runway drops to 4.2 months in the combined scenario. But look at the trace — the executor called `simulate_scenario(hire)` and `simulate_scenario(ar_delay)` separately. It never called a combined simulation. The 4.2 is a number the LLM **made up by mentally combining two deltas**. In a real CFO meeting, this would have triggered a $140k hire we couldn't actually afford."

**Why this is architectural, not a typo:**
> "The Judge gave it 5/5 on instruction-adherence — the agent did answer the question. The number was just wrong. So a single rubric axis wasn't catching it. We needed a *cross-cutting* fix."

---

## Minute 1:30–3:00 — The three-layer fix

**On screen:** Side-by-side of `PLANNER_SYSTEM_V3`, `EXECUTOR_SYSTEM_V3`, and `JUDGE_SYSTEM_V3` in `agent/orchestrator.py`. Highlight one specific clause from each.

**Script.**
> "We fixed this at three layers, because no single layer was sufficient.
>
> **Layer 1 — Executor prompt.** We added a hard rule in v3: 'If you state a number that isn't in a tool result, you have failed.' Failure language, not suggestion language. That alone reduced hallucination rate roughly in half — but not to zero.
>
> **Layer 2 — Judge prompt.** We added `hallucination_check` to the rubric and made citations *mandatory* — the judge cannot award full marks without quoting the specific trace step that justifies the score. So if the executor states a number, the judge has to find the tool call that produced it, or it can't give a 5.
>
> **Layer 3 — Tool design.** We rewrote `simulate_scenario` to return a *complete* baseline-vs-scenario-vs-delta-vs-verdict struct. That way the executor never has to mentally combine two tool outputs — combined scenarios are a *single* tool call with stacked deltas."

---

## Minute 3:00–4:00 — Live re-run with the fix

**On screen:** Run cell 14 (seed tests) or cell 24 (eval sweep) and stream the trace into the notebook. Show the same kind of question — "compare hiring + MegaCorp paying 45 days late" — and walk through:

1. Planner output: `scenarios_to_compare: ["baseline", "hire_q2", "hire_q2_plus_ar_delay"]`.
2. Executor: three discrete `simulate_scenario` calls, each cited in the `<answer> Numbers` block.
3. Judge verdict: `hallucination_check: 5` with citation `"Executor tool_call #3"`.

**Script.**
> "Same question, post-fix. Three explicit tool calls. Every number in the answer cites a trace step. The judge gives it 5/5 on hallucination — and shows its work."

---

## Minute 4:00–4:40 — What this taught us about agentic systems

**Script.**
> "Three takeaways we'd ship as advice:
>
> 1. **Failure language beats suggestion language.** 'You must cite a tool' didn't work. 'If you don't cite a tool, you have failed' did. LLMs respond to consequence framing.
> 2. **The judge needs citations or the judge is theater.** Without forced citations, our v1 judge gave 4.5/5 to obviously hallucinated answers. The rubric is only as honest as its evidence requirement.
> 3. **Tool design is prompt engineering.** Returning a complete struct from `simulate_scenario` did more than any prompt fix — it removed the *opportunity* for the LLM to do arithmetic. The most reliable safety mechanism is the one the LLM doesn't have to remember to use."

---

## Minute 4:40–5:00 — Outro

**On screen:** Architecture diagram from `ARCHITECTURE.md` §5, or notebook §13.

**Script.**
> "Code, traces, judge verdicts, and the full PVC log are in the submission package. Thank you."

---

## Recording checklist (for the person making the video)

- [ ] Screen recording at 1080p, 30 fps. OBS or QuickTime is fine.
- [ ] Mic check — talk through one full sentence before the take.
- [ ] Hide the API key. Either (a) set it in Colab secrets so it never appears, or (b) `unset ANTHROPIC_API_KEY` and rely on cached outputs in the notebook.
- [ ] Close Slack, email, browser notifications.
- [ ] Run the notebook **once** before recording so the cells have outputs — you do not want to wait for Sonnet during the take.
- [ ] Aim for 4:30–5:00. Going over 5:00 risks the grader cutting it.
- [ ] Export as MP4. Upload unlisted to YouTube **or** include the file in the submission zip — check what the LMS accepts.
- [ ] Title: "Cash Flow Runway Advisor — Failure Post-Mortem (Group 4)"
