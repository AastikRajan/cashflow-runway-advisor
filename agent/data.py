"""Synthetic accounting data generator for OrbitSaaS, a fictional B2B SaaS company.

Generates 18 months of:
  - invoices (with intentional late-payment patterns)
  - expenses (salaries, software, marketing, legal, office, with seasonality)
  - cash_snapshots (month-end balances)
  - customers (tiers, payment behavior)

Patterns intentionally planted so the agent has something to discover:
  - MegaCorp: ~40% of revenue, pays 30-45 days late on average
  - Summer revenue dip (Jun-Aug), Q4 spike
  - One-time legal expense shock in month 8
  - Gradual headcount growth (rising salary expense)
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

SEED = 42
START_DATE = date(2024, 11, 1)  # 18 months ending 2026-04-30
MONTHS = 18

CUSTOMERS = [
    # (name, tier, base_monthly_arr, avg_late_days, lateness_jitter)
    ("MegaCorp Industries", "enterprise", 42_000, 38, 7),  # the planted whale
    ("Northwind Labs",      "mid_market", 12_000, 8,  4),
    ("Acme Robotics",       "mid_market", 9_500,  5,  3),
    ("Bluefin Analytics",   "mid_market", 8_000,  10, 5),
    ("Pioneer Health",      "smb",        4_500,  3,  2),
    ("Cobalt Finance",      "smb",        3_800,  2,  2),
    ("Quanta Logistics",    "smb",        3_200,  6,  3),
    ("Helio Studios",       "smb",        2_400,  4,  3),
    ("Drift Mobile",        "smb",        2_100,  1,  2),
    ("Lumen Retail",        "smb",        1_800,  2,  2),
]

EXPENSE_CATEGORIES = {
    "salaries":  {"base": 62_000, "growth": 1_800, "variance": 1_500, "recurring": True},
    "software":  {"base": 6_500,  "growth": 120,   "variance": 800,   "recurring": True},
    "marketing": {"base": 8_000,  "growth": 200,   "variance": 2_400, "recurring": True},
    "office":    {"base": 4_200,  "growth": 50,    "variance": 600,   "recurring": True},
    "legal":     {"base": 1_200,  "growth": 0,     "variance": 400,   "recurring": False},
    "infra":     {"base": 5_800,  "growth": 250,   "variance": 700,   "recurring": True},
}

# Plant a one-time expense shock in month 8 (legal settlement)
ONE_TIME_SHOCKS = [
    (8, "legal",   45_000, "Trademark dispute settlement"),
    (12, "office", 18_000, "Office expansion deposit"),
]

OPENING_CASH = 720_000  # starting cash balance

def _seasonal_multiplier(month_index: int) -> float:
    """Revenue seasonality: dip in summer, Q4 spike."""
    cal_month = (START_DATE.month - 1 + month_index) % 12 + 1
    if cal_month in (6, 7, 8):    # summer dip
        return 0.88
    if cal_month in (10, 11, 12): # Q4 spike
        return 1.12
    return 1.0

def _expense_seasonal(month_index: int, category: str) -> float:
    cal_month = (START_DATE.month - 1 + month_index) % 12 + 1
    if category == "marketing" and cal_month in (10, 11, 12):
        return 1.35  # Q4 marketing push
    if category == "marketing" and cal_month in (6, 7, 8):
        return 0.75
    return 1.0

def _month_end(month_index: int) -> date:
    """End-of-month date for the i-th month after START_DATE."""
    year = START_DATE.year + (START_DATE.month - 1 + month_index + 1) // 12
    month = (START_DATE.month - 1 + month_index + 1) % 12 + 1
    if month == 1:
        return date(year, 1, 1) - timedelta(days=1)
    return date(year, month, 1) - timedelta(days=1)

def _month_start(month_index: int) -> date:
    year = START_DATE.year + (START_DATE.month - 1 + month_index) // 12
    month = (START_DATE.month - 1 + month_index) % 12 + 1
    return date(year, month, 1)

@dataclass
class FinancialDataset:
    invoices: pd.DataFrame
    expenses: pd.DataFrame
    cash_snapshots: pd.DataFrame
    customers: pd.DataFrame

    def to_sqlite(self, path: str | Path = ":memory:") -> sqlite3.Connection:
        conn = sqlite3.connect(str(path),check_same_thread=False)
        self.invoices.to_sql("invoices", conn, if_exists="replace", index=False)
        self.expenses.to_sql("expenses", conn, if_exists="replace", index=False)
        self.cash_snapshots.to_sql("cash_snapshots", conn, if_exists="replace", index=False)
        self.customers.to_sql("customers", conn, if_exists="replace", index=False)
        conn.commit()
        return conn

def generate(seed: int = SEED) -> FinancialDataset:
    rng = random.Random(seed)

    # ----- customers
    customers_rows = []
    for cid, (name, tier, arr, late, jitter) in enumerate(CUSTOMERS, start=1):
        customers_rows.append({
            "customer_id": cid,
            "name": name,
            "tier": tier,
            "monthly_arr": arr,
            "avg_payment_days_late": late,
            "lateness_jitter": jitter,
        })
    customers_df = pd.DataFrame(customers_rows)

    # ----- invoices: one per customer per month
    invoices_rows = []
    invoice_id = 1000
    for m in range(MONTHS):
        seasonal = _seasonal_multiplier(m)
        for cid, (name, tier, arr, late, jitter) in enumerate(CUSTOMERS, start=1):
            issue = _month_start(m)
            due = issue + timedelta(days=30)
            amount_noise = rng.uniform(0.92, 1.08)
            amount = round(arr * seasonal * amount_noise, 2)

            # paid_date and status; current month invoices may still be unpaid
            late_days = max(0, int(rng.gauss(late, max(jitter, 1))))
            target_pay = due + timedelta(days=late_days)
            today = _month_end(MONTHS - 1)  # "as of" the last data point

            if target_pay <= today:
                paid_date = target_pay
                status = "paid"
            else:
                paid_date = None
                # if past due but unpaid -> outstanding/late, else open
                status = "late" if due < today else "open"

            invoices_rows.append({
                "invoice_id": invoice_id,
                "customer_id": cid,
                "customer_name": name,
                "amount": amount,
                "issue_date": issue.isoformat(),
                "due_date": due.isoformat(),
                "paid_date": paid_date.isoformat() if paid_date else None,
                "status": status,
            })
            invoice_id += 1
    invoices_df = pd.DataFrame(invoices_rows)

    # ----- expenses
    expenses_rows = []
    expense_id = 5000
    for m in range(MONTHS):
        for cat, spec in EXPENSE_CATEGORIES.items():
            seasonal = _expense_seasonal(m, cat)
            base = spec["base"] + spec["growth"] * m
            noise = rng.gauss(0, spec["variance"])
            amount = max(0, round((base + noise) * seasonal, 2))
            txn_date = _month_start(m) + timedelta(days=rng.randint(0, 27))
            expenses_rows.append({
                "expense_id": expense_id,
                "date": txn_date.isoformat(),
                "category": cat,
                "amount": amount,
                "description": f"{cat.title()} - month {m+1}",
                "is_recurring": int(spec["recurring"]),
            })
            expense_id += 1
        # one-time shocks
        for shock_month, shock_cat, shock_amt, shock_desc in ONE_TIME_SHOCKS:
            if shock_month == m:
                expenses_rows.append({
                    "expense_id": expense_id,
                    "date": (_month_start(m) + timedelta(days=15)).isoformat(),
                    "category": shock_cat,
                    "amount": shock_amt,
                    "description": shock_desc,
                    "is_recurring": 0,
                })
                expense_id += 1
    expenses_df = pd.DataFrame(expenses_rows)

    # ----- cash snapshots: month-end, derived from opening + collections - expenses
    cash_rows = []
    cash = OPENING_CASH
    invoices_df["_paid_dt"] = pd.to_datetime(invoices_df["paid_date"])
    expenses_df["_dt"] = pd.to_datetime(expenses_df["date"])
    for m in range(MONTHS):
        ms, me = _month_start(m), _month_end(m)
        collected = invoices_df[
            invoices_df["_paid_dt"].between(pd.Timestamp(ms), pd.Timestamp(me))
        ]["amount"].sum()
        spent = expenses_df[
            expenses_df["_dt"].between(pd.Timestamp(ms), pd.Timestamp(me))
        ]["amount"].sum()
        cash = cash + float(collected) - float(spent)
        cash_rows.append({
            "snapshot_date": me.isoformat(),
            "cash_balance": round(cash, 2),
            "collections_in_month": round(float(collected), 2),
            "expenses_in_month": round(float(spent), 2),
        })
    cash_df = pd.DataFrame(cash_rows)

    invoices_df = invoices_df.drop(columns=["_paid_dt"])
    expenses_df = expenses_df.drop(columns=["_dt"])

    return FinancialDataset(
        invoices=invoices_df,
        expenses=expenses_df,
        cash_snapshots=cash_df,
        customers=customers_df,
    )
