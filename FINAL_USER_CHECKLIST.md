# Your Final Checklist — Minimum Tasks Before Submission

Everything else is done. These are the only things **you** have to do yourself.

---

## ✅ Done for you (no action needed)

- Notebook end-to-end (cells 0–37): synthetic data, 3 tools, Planner+Executor, guardrails, telemetry, judge, 50 synthetic variants, consistency, PVC log, ADRs, mermaid diagram, red-team, FinOps, artifact saving.
- `WEEK8_SUBMISSION_README.md` — rubric → artifact map (the grader's entry point).
- `PERFORMANCE_REPORT.md` — eval matrix, consistency, cost-per-success, latency, KPIs.
- `ARCHITECTURE.md` — 4 ADRs + Mermaid diagram, standalone.
- `DEMO_VIDEO_SCRIPT.md` — minute-by-minute script for the 5-minute video.
- `app.py` + `static/index.html` — optional FastAPI frontend (sidequest, not required for grading).

---

## 🎯 What YOU need to do (≈90 minutes total)

### 1. Run the notebook end-to-end **once** (≈20 min, ≈$0.50)

```
Open: cash_flow_runway_advisor-5.ipynb in Colab
Set:  ANTHROPIC_API_KEY in Colab Secrets (left sidebar)
Click: Runtime → Run all
Wait: ~15-18 minutes for the 50-variant eval to finish
Verify: §16 "Save Artifacts" cell prints an artifacts/<timestamp>/ path
```

If a cell fails, the most common cause is the API key not being in Colab Secrets. Fix and re-run only that cell.

### 2. Confirm the artifacts saved (≈2 min)

After §16 runs you should see:
```
artifacts/<timestamp>/
  ├── traces.json
  ├── verdicts.json
  ├── summary.json
  └── consistency.json
```
Download the folder — it's part of the submission.

### 3. Record the 5-minute video (≈45 min including a retake)

- Open `DEMO_VIDEO_SCRIPT.md`. It's a minute-by-minute walkthrough.
- Record screen + mic (OBS / QuickTime / Loom).
- Aim 4:30–5:00. Don't go over.
- Export MP4. Title: *"Cash Flow Runway Advisor — Failure Post-Mortem (Group 4)"*.

### 4. Package the submission (≈10 min)

Zip the project folder, but **exclude**:
- `__pycache__/` directories
- any file containing your API key
- the `.git` folder if you initialized one

What the zip should contain:
```
cash_flow_runway_advisor-5.ipynb     ← run, with outputs
WEEK8_SUBMISSION_README.md            ← start here for grader
PERFORMANCE_REPORT.md
ARCHITECTURE.md
DEMO_VIDEO_SCRIPT.md
GAI Project_Cash Flow Runway Advisor Proposal.md   ← Phase 1
agent/                                ← orchestrator, tools, data
  ├── __init__.py
  ├── data.py
  ├── orchestrator.py
  └── tools.py
app.py                                ← optional FastAPI frontend
static/index.html
requirements_web.txt
artifacts/<timestamp>/                ← from step 2
demo_video.mp4                        ← or a YouTube unlisted link in README
```

### 5. Submit (≈5 min)

Upload the zip + demo video to the course LMS. Double-check:

- [ ] API key is **not** in any file (grep for `sk-ant-` to be sure)
- [ ] Notebook has outputs (cells 9, 19, 22, 26, 27, 29, 35 should show data)
- [ ] Video plays on a fresh device / browser
- [ ] `WEEK8_SUBMISSION_README.md` is at the top level of the zip

---

## 🛑 Don't do these (auto-deductions)

- ❌ Use a chatbot UI as the demo — −10 pts. Show the **notebook** and **trace JSON**.
- ❌ Hard-code answers to test cases — −10 pts. The success classifier rejects non-`<answer>` outputs anyway.
- ❌ Commit your API key — −5 pts. Run `grep -r "sk-ant" .` before zipping.

---

## TL;DR — the 4 things only you can do

1. Run the notebook once with your API key.
2. Download the `artifacts/` folder it produces.
3. Record the 5-minute video using `DEMO_VIDEO_SCRIPT.md`.
4. Zip everything (minus API keys, `__pycache__`, `.git`) and upload.

That's it.
