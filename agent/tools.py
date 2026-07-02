"""Three deterministic tools the agent will call.

Design rules:
  1. Every tool returns structured JSON-serializable output (never just a string).
  2. Every tool validates inputs and returns an explicit error envelope on bad input
     so the agent can recover instead of crashing the loop.
  3. Numeric calculations live HERE, not in the LLM (per ADR: AI vs Rule-Based).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pandas as pd

# ----------- shared helpers --------------------------------------------------

def _ok(**payload) -> dict:
    return {"status": "ok", **payload}

def _err(message: str, **extra) -> dict:
    return {"status": "error", "error": message, **extra}

def _safe_select(sql: str) -> bool:
    """Reject anything that isn't a single SELECT statement.

    Defense-in-depth: even though the agent is supposed to behave, we don't want
    a hallucinated DROP/UPDATE to corrupt the in-memory database.
    """
    s = sql.strip().rstrip(";").lower()
    if ";" in s:
        return False
    if not s.startswith(("select", "with ")):
        return False
    forbidden = ("insert", "update", "delete", "drop", "alter", "create",
                 "attach", "pragma", "replace ", "vacuum")
    return not any(tok in s for tok in forbidden)

# ----------- Tool 1: query_financials ---------------------------------------

def query_financials(conn: sqlite3.Connection, sql: str, max_rows: int = 50) -> dict:
    """Run a read-only SQL query against the in-memory accounting DB.

    Tables available:
      invoices(invoice_id, customer_id, customer_name, amount,
               issue_date, due_date, paid_date, status)
      expenses(expense_id, date, category, amount, description, is_recurring)
      cash_snapshots(snapshot_date, cash_balance,
                     collections_in_month, expenses_in_month)
      customers(customer_id, name, tier, monthly_arr,
                avg_payment_days_late, lateness_jitter)
    """
    if not isinstance(sql, str) or not sql.strip():
        return _err("empty SQL")
    if not _safe_select(sql):
        return _err("only single SELECT statements are permitted")
    try:
        df = pd.read_sql_query(sql, conn)
    except Exception as e:
        return _err(f"SQL execution failed: {e}", sql=sql)

    truncated = len(df) > max_rows
    if truncated:
        df = df.head(max_rows)
    rows = df.to_dict(orient="records")
    # Round floats so the LLM doesn't choke on noisy decimals
    for r in rows:
        for k, v in list(r.items()):
            if isinstance(v, float):
                r[k] = round(v, 2)
    return _ok(rows=rows, row_count=len(rows), truncated=truncated, columns=list(df.columns))

# ----------- Tool 2: calculate_runway ---------------------------------------

@dataclass
class RunwayResult:
    cash_balance: float
    monthly_burn: float
    monthly_collections: float
    monthly_net_burn: float
    runway_months: float
    runway_low: float       # pessimistic (burn + 1 std)
    runway_high: float      # optimistic (burn - 1 std)
    burn_stdev: float
    burn_window_months: int
    safety_threshold_months: float
    below_safety_threshold: bool

    def to_dict(self) -> dict:
        return self.__dict__

def calculate_runway(
    conn: sqlite3.Connection,
    cash_balance: float | None = None,
    monthly_burn: float | None = None,
    scenario_deltas: dict | None = None,
    burn_window_months: int = 3,
    safety_threshold_months: float = 6.0,
) -> dict:
    """Compute runway off a rolling burn window, with sensitivity bounds.

    Conventions:
      monthly_burn       = average gross expenses over the trailing window
      monthly_collections= average collections over the same window
      monthly_net_burn   = monthly_burn - monthly_collections
      runway_months      = cash / monthly_net_burn   (clipped at 0/inf)

    scenario_deltas may include any of:
      cash_delta              -> add to cash_balance
      monthly_expense_delta   -> add to monthly_burn (e.g. +11_667 for $140k hire)
      monthly_collection_delta-> add to monthly_collections
      ar_delay_days           -> defer N days of collections (haircut)
    """
    try:
        snap = pd.read_sql_query(
            "SELECT * FROM cash_snapshots ORDER BY snapshot_date DESC", conn
        )
    except Exception as e:
        return _err(f"could not read cash_snapshots: {e}")

    if snap.empty:
        return _err("no cash snapshots available")

    if cash_balance is None:
        cash_balance = float(snap.iloc[0]["cash_balance"])

    window = snap.head(max(1, burn_window_months))
    window_collections = window["collections_in_month"].astype(float)
    window_expenses = window["expenses_in_month"].astype(float)

    avg_collections = float(window_collections.mean())
    if monthly_burn is None:
        monthly_burn = float(window_expenses.mean())
    burn_stdev = float(window_expenses.std(ddof=0)) if len(window_expenses) > 1 else 0.0

    deltas = scenario_deltas or {}
    if not isinstance(deltas, dict):
        return _err("scenario_deltas must be an object")

    cash_balance += float(deltas.get("cash_delta", 0) or 0)
    monthly_burn += float(deltas.get("monthly_expense_delta", 0) or 0)
    avg_collections += float(deltas.get("monthly_collection_delta", 0) or 0)

    ar_delay = float(deltas.get("ar_delay_days", 0) or 0)
    if ar_delay > 0:
        # AR delay is a timing issue, not a revenue loss: customers eventually
        # pay, but the cash arrives ar_delay days later. Model as a one-time
        # hit to opening cash equal to ar_delay/30 months of collections.
        # Average go-forward collections are unchanged.
        deferred_cash = avg_collections * (ar_delay / 30.0)
        cash_balance -= deferred_cash

    net_burn = monthly_burn - avg_collections

    def _runway(net):
        if net <= 0:
            return float("inf")
        return cash_balance / net

    runway = _runway(net_burn)
    runway_low = _runway(net_burn + burn_stdev)
    runway_high = _runway(max(0.01, net_burn - burn_stdev))

    res = RunwayResult(
        cash_balance=round(cash_balance, 2),
        monthly_burn=round(monthly_burn, 2),
        monthly_collections=round(avg_collections, 2),
        monthly_net_burn=round(net_burn, 2),
        runway_months=round(runway, 2) if runway != float("inf") else None,
        runway_low=round(runway_low, 2) if runway_low != float("inf") else None,
        runway_high=round(runway_high, 2) if runway_high != float("inf") else None,
        burn_stdev=round(burn_stdev, 2),
        burn_window_months=burn_window_months,
        safety_threshold_months=safety_threshold_months,
        below_safety_threshold=(runway < safety_threshold_months),
    )
    return _ok(**res.to_dict())

# ----------- Tool 3: simulate_scenario --------------------------------------

KNOWN_ACTIONS = {
    "hire":            "Add headcount; params: salary_annual, start_month_offset (default 0)",
    "delay_hire":      "Postpone a hire; params: salary_annual, delay_months",
    "cut_spend":       "Cut a recurring category by %; params: category, pct",
    "ar_delay":        "Major customer pays late; params: days, customer_share (0-1)",
    "price_increase":  "Raise prices; params: pct (applies to monthly collections)",
    "raise_capital":   "One-time injection; params: amount",
}

def simulate_scenario(
    conn: sqlite3.Connection,
    action: str,
    params: dict | None = None,
    timeframe_months: int = 12,
) -> dict:
    """Compare a baseline runway vs an action-modified runway.

    Returns a structured comparison so the agent can interpret the trade-off
    instead of guessing at numbers itself.
    """
    if action not in KNOWN_ACTIONS:
        return _err(
            f"unknown action '{action}'",
            known_actions=KNOWN_ACTIONS,
        )
    params = params or {}
    deltas: dict[str, float] = {}
    description = ""
    growth_risk: dict = {}

    if action == "hire":
        salary = float(params.get("salary_annual", 0))
        if salary <= 0:
            return _err("hire requires salary_annual > 0")
        monthly = salary / 12
        deltas["monthly_expense_delta"] = monthly
        description = f"Hire at ${salary:,.0f}/yr (+${monthly:,.0f}/month burn)"

    elif action == "delay_hire":
        salary = float(params.get("salary_annual", 0))
        delay = int(params.get("delay_months", 3))
        if salary <= 0 or delay <= 0:
            return _err("delay_hire requires salary_annual > 0 and delay_months > 0")
        # Average monthly impact over the timeframe is reduced
        months_active = max(0, timeframe_months - delay)
        avg_monthly = (salary / 12) * (months_active / timeframe_months)
        deltas["monthly_expense_delta"] = avg_monthly
        description = (
            f"Delay hire {delay} months; avg incremental burn over {timeframe_months}mo = "
            f"${avg_monthly:,.0f}/month"
        )

    elif action == "cut_spend":
        # Need to look up current avg category spend to compute the cut value.
        category = str(params.get("category", "")).lower()
        pct = float(params.get("pct", 0))
        if not category or pct <= 0:
            return _err("cut_spend requires category and pct > 0")
        df = pd.read_sql_query(
            "SELECT category, AVG(amount) AS avg_amt "
            "FROM expenses WHERE is_recurring = 1 GROUP BY category",
            conn,
        )
        match = df[df["category"].str.lower() == category]
        if match.empty:
            return _err(
                f"category '{category}' not found in recurring expenses",
                available=df["category"].tolist(),
            )
        cur_monthly = float(match.iloc[0]["avg_amt"])
        savings = cur_monthly * (pct / 100.0)
        deltas["monthly_expense_delta"] = -savings
        description = (
            f"Cut {category} by {pct:.0f}% (~${savings:,.0f}/month savings, "
            f"baseline ${cur_monthly:,.0f}/month)"
        )
        if category == "marketing":
            risk_pct = round((savings / 1_000) * 0.5, 1)
            growth_risk = {
                "growth_risk_pct_per_month": risk_pct,
                "pipeline_recovery_months": 3,
                "note": (
                    f"Cutting ${savings:,.0f}/mo from marketing is estimated to"
                    f" reduce MoM revenue growth by ~{risk_pct} pp for ~3 months"
                    f" (rule: $1k/mo cut ≈ 0.5 pp growth drag; ROI lag ≈ 3 months)."
                ),
            }

    elif action == "ar_delay":
        days = float(params.get("days", 30))
        share = float(params.get("customer_share", 0.4))  # MegaCorp is ~40%
        if days <= 0 or not (0 < share <= 1):
            return _err("ar_delay requires days > 0 and 0 < customer_share <= 1")
        deltas["ar_delay_days"] = days * share
        description = f"AR delay: {days:.0f} extra days on {share*100:.0f}% of revenue"

    elif action == "price_increase":
        pct = float(params.get("pct", 0))
        if pct <= 0:
            return _err("price_increase requires pct > 0")
        # Apply to current avg collections - need baseline first
        baseline = calculate_runway(conn)
        if baseline.get("status") != "ok":
            return baseline
        uplift = baseline["monthly_collections"] * (pct / 100.0)
        deltas["monthly_collection_delta"] = uplift
        description = f"Price increase {pct:.0f}% (+${uplift:,.0f}/month collections)"

    elif action == "raise_capital":
        amount = float(params.get("amount", 0))
        if amount <= 0:
            return _err("raise_capital requires amount > 0")
        deltas["cash_delta"] = amount
        description = f"Raise ${amount:,.0f} (one-time cash injection)"

    baseline = calculate_runway(conn)
    if baseline.get("status") != "ok":
        return baseline
    scenario = calculate_runway(conn, scenario_deltas=deltas)
    if scenario.get("status") != "ok":
        return scenario

    def _diff(key):
        b, s = baseline.get(key), scenario.get(key)
        if b is None or s is None:
            return None
        return round(s - b, 2)

    comparison = {
        "action": action,
        "description": description,
        "params": params,
        "deltas_applied": deltas,
        "baseline": {
            "runway_months": baseline["runway_months"],
            "monthly_net_burn": baseline["monthly_net_burn"],
            "cash_balance": baseline["cash_balance"],
        },
        "scenario": {
            "runway_months": scenario["runway_months"],
            "monthly_net_burn": scenario["monthly_net_burn"],
            "cash_balance": scenario["cash_balance"],
            "below_safety_threshold": scenario["below_safety_threshold"],
        },
        "delta": {
            "runway_months": _diff("runway_months"),
            "monthly_net_burn": _diff("monthly_net_burn"),
        },
        "verdict": _verdict(baseline, scenario),
        "growth_risk": growth_risk,
    }
    return _ok(**comparison)

def _verdict(baseline: dict, scenario: dict) -> str:
    b = baseline.get("runway_months")
    s = scenario.get("runway_months")
    safety = baseline.get("safety_threshold_months", 6)
    if b is None or s is None:
        return "indeterminate"
    if scenario.get("below_safety_threshold") and not baseline.get("below_safety_threshold"):
        return "warning_breaches_safety_threshold"
    if s >= b:
        return "improves_or_neutral"
    if s >= safety:
        return "acceptable_but_shorter_runway"
    return "warning_below_safety_threshold"

# ----------- Anthropic tool schemas (single source of truth) -----------------

TOOL_SCHEMAS = [
    {
        "name": "query_financials",
        "description": (
            "Run a read-only SQL SELECT against the in-memory accounting database. "
            "Tables: invoices, expenses, cash_snapshots, customers. "
            "Use this when you need raw numbers, lists, or aggregates. "
            "Only one SELECT (or WITH...SELECT) statement at a time. "
            "Always quote table/column names exactly as listed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "A single SQLite SELECT statement.",
                },
            },
            "required": ["sql"],
        },
    },
    {
        "name": "calculate_runway",
        "description": (
            "Deterministically compute cash runway in months from the latest "
            "cash snapshot and a rolling 3-month burn window. Optionally apply "
            "scenario_deltas to model what-ifs WITHOUT calling simulate_scenario. "
            "Returns runway_months plus low/high sensitivity bounds. "
            "Prefer simulate_scenario for named actions like hire/cut_spend; use this "
            "tool when you want raw numeric control."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cash_balance": {"type": "number", "description": "Override cash; default = latest snapshot."},
                "monthly_burn": {"type": "number", "description": "Override burn; default = trailing-window average."},
                "scenario_deltas": {
                    "type": "object",
                    "description": (
                        "Optional adjustments. Keys: cash_delta, "
                        "monthly_expense_delta, monthly_collection_delta, ar_delay_days."
                    ),
                },
                "burn_window_months": {"type": "integer", "description": "Trailing months to average. Default 3."},
                "safety_threshold_months": {"type": "number", "description": "Months considered 'safe'. Default 6."},
            },
        },
    },
    {
        "name": "simulate_scenario",
        "description": (
            "Compare the baseline runway against a named action. Returns a "
            "structured comparison (baseline vs scenario vs delta vs verdict). "
            "Use this for hire/delay_hire/cut_spend/ar_delay/price_increase/raise_capital."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(KNOWN_ACTIONS.keys()),
                    "description": "Named scenario type.",
                },
                "params": {
                    "type": "object",
                    "description": (
                        "Action-specific parameters. "
                        "hire/delay_hire: salary_annual, delay_months. "
                        "cut_spend: category, pct. "
                        "ar_delay: days, customer_share. "
                        "price_increase: pct. "
                        "raise_capital: amount."
                    ),
                },
                "timeframe_months": {"type": "integer", "description": "Default 12."},
            },
            "required": ["action"],
        },
    },
]

def dispatch(conn: sqlite3.Connection, name: str, tool_input: dict) -> dict:
    """Single entry point used by the executor agent's tool_use loop."""
    try:
        if name == "query_financials":
            return query_financials(conn, **tool_input)
        if name == "calculate_runway":
            return calculate_runway(conn, **tool_input)
        if name == "simulate_scenario":
            return simulate_scenario(conn, **tool_input)
        return _err(f"unknown tool '{name}'")
    except TypeError as e:
        return _err(f"bad arguments for {name}: {e}", received=tool_input)
    except Exception as e:
        return _err(f"{name} crashed: {e}")
