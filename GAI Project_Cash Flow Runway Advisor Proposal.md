# **Agentic AI Project Proposal** **Cash Flow Runway Advisor**

Course: Generative AI  |  Team Topic Proposal  |  Track: Claude Agent SDK

*Group 4: Xing Wang, Yi Lu, [Aastik Rajan](https://jhu.instructure.com/courses/113742/users/157420)*

| Business user | Core decision | Why agentic AI |
| :---: | :---: | ----- |
| Founder / CFO | Can we hire, delay spending, or raise now? | Requires tool use, deterministic math, scenario comparison, and ambiguity resolution |

# **1\. Executive Summary & Problem Statement**

**Business Problem:** Small business founders and CFOs routinely face high-stakes cash questions such as “Can I afford to hire in Q2?” or “What happens if my biggest client pays late again?” Existing dashboards describe the current state, but they do not compute multi-variable what-if scenarios from raw accounting data.

**Value Proposition:** We propose a conversational finance agent that ingests QuickBooks-style transaction data and returns computed runway projections, scenario comparisons, and risk-aware recommendations. The target outcome is to replace 2–3 hours of spreadsheet analysis per decision with a traceable answer in under two minutes.

**Agentic Justification:** This is not a single-prompt problem. The system must (1) interpret an ambiguous user request, (2) query structured financial records, (3) perform deterministic runway and sensitivity calculations, (4) simulate alternative actions, and (5) synthesize a recommendation with explicit uncertainty. Zero-shot prompting lacks data access and control; basic RAG retrieves text but cannot guarantee correct math or scenario logic.

# **2\. Proposed System Architecture**

**High-Level Flow:** User question \-\> Planner Agent \-\> Executor Agent \-\> Tools \-\> Synthesized recommendation \-\> Judge / logging layer. The Planner decomposes the request and decides which tools are needed. The Executor runs tools, merges outputs, and drafts the final answer. The Judge is not part of the live answering path, but it scores traces during evaluation.

**AI vs. Rules Split:** AI is used where semantic interpretation and trade-off reasoning matter: query routing, NL-to-SQL generation, and final scenario comparison. Rules / deterministic code are used where correctness matters most: runway math, thresholds, iteration caps, token caps, and tool timeouts.

**Platform Choice:** We choose the Claude Agent SDK over LangGraph for Phase 1 because the project rubric rewards explicit planner/executor multi-agent design, prompt-governed behavior, and careful model-role separation. The SDK also makes it easy to enforce financially conservative output behavior, such as forbidding overconfident hire/no-hire advice.

# **3\. Tooling & Data Strategy**

**Tool Inventory:** query\_financials(table, filters) retrieves structured data from a SQLite-backed mock QuickBooks database; calculate\_runway(monthly\_burn, cash\_balance, scenario\_deltas) computes runway months, sensitivity ranges, and safety-threshold checks using pandas; simulate\_scenario(base\_state, action, timeframe) compares hire / delay / cut-spend options and returns a structured comparison table.

**Source Data:** We will use LLM-generated synthetic CSV files representing 18 months of accounting activity for a fictional SaaS company, including invoices, expenses, AR aging, and cash snapshots. Synthetic data is appropriate here because it avoids privacy issues while allowing us to plant analytically useful patterns such as seasonality, a late-paying enterprise client, and one-time expense shocks. The generated dataset and the generation prompt will both be committed to the project repository so that evaluation runs are fully reproducible and the Consistency Score reflects agent variance rather than data variance.

**Analytic Logic:** Monthly burn will be computed as a rolling 3-month average of expenses minus collections. Runway is cash divided by burn under a baseline scenario, then recomputed under modified assumptions such as delayed receivables, a new hire, or spend cuts. The explicit goal is deterministic calculation first, model reasoning second.

# **4\. FinOps & Resource Plan**

**Model Selection:** Planner: Claude Sonnet 4.6 for decomposition and instruction-following on ambiguous user questions. Executor: Claude Haiku 4.5 for lower-cost tool orchestration and synthesis. Judge: Claude Sonnet 4.6 because rubric-based grading requires stronger reasoning and better calibration.

**Guardrails:** Hard guardrails will include max\_iterations \= 7, max\_total\_tokens \= 15,000 per task, and tool timeouts of 10 seconds. If a limit is hit, the system will return a partial result with an explicit incomplete flag rather than failing silently.

**Cost Plan:** Estimated evaluation load is approximately 4 model calls per task x 50 synthetic tests x 3 consistency runs, or about 600 calls. At mixed Sonnet / Haiku usage, we estimate roughly $8–12 for a full evaluation sweep and reserve a $25 budget cap to absorb retries and debugging.

# **5\. Evaluation & Judge Design**

**Success Metrics:** The primary KPIs are calculation accuracy, scenario completeness, and risk disclosure. We will grade whether runway values fall within ±5% of ground truth, whether all user-implied scenarios were analyzed, and whether the agent flags uncertainty such as AR variance, customer concentration risk, and one-time versus recurring effects.

**Judge Design:** A separate Claude Sonnet 4.6 judge will read the full trace — user request, planner steps, tool outputs, and final answer — and score it against a structured rubric. The Judge is given ground-truth values for synthetic test cases so it can evaluate calculation accuracy deterministically. The Judge system prompt is shown below.

PROMPT:

You are a senior FP\&A analyst grading a junior financial analyst

agent. You will receive:

  \- The user's original question

  \- The agent's full reasoning trace (planner thoughts, tool calls,

    tool outputs)

  \- The agent's final answer

  \- Ground-truth values for this synthetic test case

Score the agent on a 1-5 scale across the following dimensions:

STANDARD RUBRIC

  1\. Instruction Adherence \-- did the agent address the user's

     actual question?

  2\. Reasoning Transparency \-- is the trace legible and logical?

  3\. Hallucination Check \-- are all numeric claims backed by tool

     output?

CUSTOM DOMAIN RUBRIC

  4\. Calculation Accuracy \-- is the runway figure within \+/- 5%

     of ground truth?

  5\. Scenario Completeness \-- were all user-implied scenarios

     actually simulated?

  6\. Risk Disclosure \-- did the agent flag at least one relevant

     uncertainty (AR variance, customer concentration, one-time

     vs. recurring expense)?

For EACH score, you MUST cite the specific trace step (e.g.,

"Executor Action 2") that justifies the score. Do not award full

marks without citation.

Return output as valid JSON with this schema:

{

  "instruction\_adherence":  {"score": 1-5, "citation": "..."},

  "reasoning\_transparency": {"score": 1-5, "citation": "..."},

  "hallucination\_check":    {"score": 1-5, "citation": "..."},

  "calculation\_accuracy":   {"score": 1-5, "citation": "..."},

  "scenario\_completeness":  {"score": 1-5, "citation": "..."},

  "risk\_disclosure":        {"score": 1-5, "citation": "..."},

  "overall\_verdict": "PASS" | "PARTIAL" | "FAIL",

  "notes": "one-sentence summary"

}

*Why this prompt:* It (a) forces citations, which prevents the Judge from hallucinating its own grades — a known failure mode of LLM-as-Judge systems, (b) separates standard vs. custom rubric axes exactly as the assignment requires, (c) returns structured JSON so you can aggregate scores programmatically for the Evaluation Matrix in Phase 2, (d) gives the Judge access to ground truth, which is legitimate because your data is synthetic.

**Test Plan:** We will handwrite 5 seed cases, expand them into 50 synthetic variants with different tones and missing-information patterns, and run a 10-question consistency test 3 times each to measure answer variance. We will also keep prompt version control notes across at least three iterations so we can document what improved and what regressed.

**Five Seed Test Cases**

• Happy path: “What is my current runway at today’s burn rate?”

• Edge case (out-of-range data): "What was my burn rate in Q3 2023?" — dataset covers only the last 18 months; agents should detect the out-of-range request and disclose the data gap rather than fabricate a value.

• Adversarial pressure: “Just answer yes or no: am I going to run out of money?”

• Complex comparison: “Compare hiring one engineer in Q2 vs. two in Q3 if our biggest client pays 45 days late.”

• Trade-off case: “If I cut marketing 30%, how much runway do I gain and what growth risk should I expect?”

# **6\. Initial Trace Example**

**Manual Trace:** User: “Can I afford to hire a $140K engineer in Q2 if my biggest client keeps paying late?” Planner: identify baseline runway, runway under hire, and sensitivity to AR delay. Executor step 1: query\_financials(last\_6\_months) \-\> observes burn of $85K, cash of $520K, and a large client representing 40% of AR with average 35-day lateness. Executor step 2: calculate\_runway(...) \-\> baseline runway 6.1 months. Executor step 3: simulate\_scenario(add\_hire\_q2\_140k, ar\_delay\_15\_days) \-\> runway drops to 4.2 months and 3.6 months in a worse-delay case. Final answer: recommend delaying the hire to Q3 unless bridge financing is secured, because the projected runway falls below a 6-month safety threshold.

**Why This Trace Matters:** The trace demonstrates all required components: multi-step reasoning, at least three tools, real computation, ambiguity resolution, and a recommendation tied to explicit evidence rather than unsupported intuition.